"""Intent hints for existing authorized actions. Dispatch remains on v1."""

PROJECT = {'actionType': 'action-binding.v2', 'resultType': 'action-binding.v2'}
CONTROL_VERBS = {
    'lights.turn_on': 'on', 'lights.turn_off': 'off',
    'covers.open': 'open', 'covers.close': 'close', 'covers.stop': 'stop',
    'awnings.extend': 'open', 'awnings.retract': 'close', 'awnings.stop': 'stop',
}
NOUNS = {
    'light': {'en': ['light', 'lights', 'lamp', 'lamps'], 'pl': ['światło', 'światła', 'świateł', 'lampa', 'lampę', 'lampy']},
    'cover': {'en': ['blind', 'blinds', 'curtain', 'curtains', 'shade', 'shades', 'shutter', 'shutters'],
              'pl': ['roleta', 'roletę', 'rolety', 'zasłona', 'zasłonę', 'zasłony', 'żaluzja', 'żaluzje', 'żaluzji']},
    'awning': {'en': ['awning', 'awnings'], 'pl': ['markiza', 'markizę', 'markizy']},
}
TIMER_VERBS = {'timers.start': 'start', 'timers.pause': 'pause', 'timers.resume': 'resume', 'timers.cancel': 'cancel'}


def project_controls(result):
    targets = []
    for target in result['targets']:
        nouns = NOUNS[target['kind']]
        actions = [{'key': item['id'], 'verb': CONTROL_VERBS[item['id']], 'nouns': nouns}
                   for item in target['capabilities'] if item['id'] in CONTROL_VERBS]
        if actions:
            targets.append({'id': target['id'], 'actions': actions})
    return {**result, 'intent': {'version': 2, 'targets': targets}}


def project_timers(result):
    bindings = [{'key': item['id'], 'verb': TIMER_VERBS[item['id']],
                 'subjects': [subject['id'] for subject in item['subjects']]}
                for item in result['actions'] if item['id'] in TIMER_VERBS]
    return {**result, 'intent': {'version': 2, 'bindings': bindings}}
