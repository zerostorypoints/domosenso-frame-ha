"""Connection proofs and administrator-approved grants, independent of HA IO."""
import base64
import hashlib
import secrets
import time
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

class Grants:
    def __init__(self, installation, records, clock=time.monotonic):
        self.installation, self.records, self.clock = installation, records, clock
        self.challenges, self.sessions, self.pending = {}, {}, {}

    def close(self, session):
        self.challenges.pop(session, None)
        self.sessions.pop(session, None)

    def begin(self, session, user, endpoint, public, name):
        if len(public)>256 or len(name)>80: raise ValueError('Invalid key or name')
        raw=base64.b64decode(public, validate=True)
        key=serialization.load_der_public_key(raw)
        if not isinstance(key,ec.EllipticCurvePublicKey) or not isinstance(key.curve,ec.SECP256R1): raise ValueError('Expected P-256')
        device=hashlib.sha256(raw).hexdigest()
        prior=self.records.get(device)
        if prior and (prior['state']!='approved' or prior['user']!=user or prior['endpoint']!=endpoint): raise ValueError('Device binding denied')
        challenge='domosenso-frame|'+self.installation+'|'+secrets.token_urlsafe(32)
        self.challenges[session]=(self.clock()+60,challenge,device,public,user,endpoint,name)
        self.sessions.pop(session,None)
        return {'challenge':challenge,'deviceId':device}

    def finish(self, session, signature):
        attempt=self.challenges.pop(session,None)
        if not attempt or attempt[0]<self.clock() or len(signature)>256: raise ValueError('Challenge expired')
        _,challenge,device,public,user,endpoint,name=attempt
        try:
            serialization.load_der_public_key(base64.b64decode(public,validate=True)).verify(base64.b64decode(signature,validate=True),challenge.encode(),ec.ECDSA(hashes.SHA256()))
        except Exception as err: raise ValueError('Invalid signature') from err
        prior=self.records.get(device)
        if prior and (prior['state']!='approved' or prior['user']!=user or prior['endpoint']!=endpoint): raise ValueError('Device binding denied')
        if not prior:
            pending=self.pending.get(device)
            if not pending or pending['expires']<self.clock():
                if len(self.pending)>=128:
                    self.pending={k:v for k,v in self.pending.items() if v['expires']>=self.clock()}
                    if len(self.pending)>=128: raise ValueError('Too many pending requests')
                self.pending[device]={'user':user,'endpoint':endpoint,'name':name,'code':f'{secrets.randbelow(1000000):06d}','state':'pending','expires':self.clock()+300}
            elif pending['user']!=user or pending['endpoint']!=endpoint: raise ValueError('Conflicting pending request')
        self.sessions[session]=(device,user,endpoint)
        return self.status(session,user)

    def status(self, session, user):
        proof=self.sessions.get(session)
        if not proof or proof[1]!=user: raise ValueError('Proof required')
        device,_,endpoint=proof
        record=self.records.get(device) or self.pending.get(device)
        if not record or record['user']!=user or record['endpoint']!=endpoint: raise ValueError('Binding mismatch')
        if record['state']=='pending' and record['expires']<self.clock(): raise ValueError('Approval expired')
        return {'deviceId':device,'state':record['state'],'endpoint':endpoint,**({'code':record['code']} if record['state']=='pending' else {})}

    def authorized(self,session,user):
        status=self.status(session,user)
        if status['state']!='approved': raise ValueError('Administrator approval required')
        return status

    def approve(self,device,code):
        pending=self.pending.get(device)
        if not pending or pending['expires']<self.clock() or not secrets.compare_digest(pending['code'],code): raise ValueError('Pending request expired or code differs')
        self.records[device]={k:v for k,v in pending.items() if k not in ('expires','code')}
        self.records[device]['state']='approved'
        del self.pending[device]

    def revoke(self,device):
        if device not in self.records: raise ValueError('Unknown device')
        self.records[device]['state']='revoked'

    def remove_revoked(self,device):
        if self.records.get(device,{}).get('state')!='revoked':raise ValueError('Revoke the device before removing its registration')
        del self.records[device]
        self.pending.pop(device,None)
        for session,proof in list(self.sessions.items()):
            if proof[0]==device:self.close(session)
        for session,attempt in list(self.challenges.items()):
            if attempt[2]==device:self.close(session)

    def devices(self):
        return [{'deviceId':k,**v} for k,v in self.records.items()]+[{'deviceId':k,**{a:b for a,b in v.items() if a!='expires'}} for k,v in self.pending.items() if v['expires']>=self.clock()]
