"""Detect private metadata changes off-loop and publish a complete snapshot."""
import asyncio
import os
import stat
from pathlib import Path


def fingerprint(config_dir):
    root=Path(config_dir)/'domosenso'
    result=[]
    if root.is_symlink():return ('invalid-root',)
    for name in ('assistant','assistant-runtime','assistant-actions','assistant-metadata'):
        directory=root/name
        try:
            info=directory.lstat();result.append((name,info.st_mode,info.st_mtime_ns,info.st_ino))
            if not stat.S_ISDIR(info.st_mode):continue
            entries=sorted(directory.iterdir(),key=lambda p:p.name)
            if len(entries)>1024:raise OSError('Too many metadata entries')
            for path in entries:
                if path.suffix!='.yaml':continue
                info=path.lstat()
                result.append((name,path.name,info.st_mode,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns))
        except FileNotFoundError:result.append((name,'missing'))
    return tuple(result)


class MetadataRefresh:
    def __init__(self,signature,load,publish,executor):
        self.signature=signature;self.load=load;self.publish=publish;self.executor=executor
        self.previous=None;self.lock=asyncio.Lock();self.closed=False

    async def check(self,force=False):
        async with self.lock:
            if self.closed:return
            try:
                signature=await self.executor(self.signature)
                if not force and signature==self.previous:return
                result=await self.executor(self.load)
                after=await self.executor(self.signature)
                if after!=signature:raise OSError('Metadata changed while reading')
            except (OSError,ValueError):
                result={};signature=None
            if self.closed:return
            self.publish(result)
            self.previous=signature

    async def close(self):
        self.closed=True
        async with self.lock:pass
