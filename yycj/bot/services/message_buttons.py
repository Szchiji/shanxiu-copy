"""Private-message keyboard schema shared by execution and admin previews.
Legacy welcome lists keep their order and two-column rows. No arbitrary callback actions.
"""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from bot.services.community import safe_url

LIMITS={'welcome':{'total':12,'columns':4},'approval':{'total':16,'columns':4}}
TARGETS={'welcome':('home','me'),'approval':('home','me','detail')}


def clean(items,context='welcome'):
    if context not in LIMITS or not isinstance(items,list):return []
    result=[]
    for i,item in enumerate(items[:64]):
        if not isinstance(item,dict):continue
        kind=item.get('type');label=str(item.get('label') or '').strip()[:32]
        target=str(item.get('target') or '').strip()
        if kind=='url':
            target=target if context=='approval' and target in ('{album}','{detail}') else safe_url(target)
        elif kind!='webapp' or target not in TARGETS[context]:continue
        if not label or not target:continue
        value={'type':kind,'label':label,'target':target}
        # Preserve legacy serialization while enriching only explicitly upgraded items.
        if 'enabled' in item:value['enabled']=item['enabled'] is not False
        if 'new_row' in item:value['new_row']=item['new_row'] is True
        else:value['new_row']=len(result)%2==0  # old welcome layout
        result.append(value)
        if len(result)>=LIMITS[context]['total']:break
    return result


def approval_config(value):
    if not isinstance(value,dict) or value.get('mode')!='custom':return {'mode':'default','items':[]}
    return {'mode':'custom','items':clean(value.get('items'),'approval')}


def approval_items(site):
    cfg=approval_config(site.get('approval_buttons'))
    if cfg['mode']=='custom':return cfg['items']
    rows=[]
    if site.get('approve_attach_detail',True):rows.append({'type':'webapp','label':'查看我的资料','target':'detail','new_row':True})
    if site.get('approve_attach_channel_btn',True):rows.append({'type':'url','label':'查看频道帖','target':'{album}','new_row':True})
    return rows


def resolve(items,context,*,base='',detail_url='',detail_webapp='',album='',private=True):
    """Pure renderer also used by preview endpoint. Missing dynamic targets disappear."""
    base=str(base or '').rstrip('/')
    if base.endswith('.html'):base=base.rsplit('/',1)[0]
    rows=[]
    for item in clean(items,context):
        if not item.get('enabled',True):continue
        kind=item['type'];target=item['target']
        if kind=='webapp':
            if not private:continue
            url=detail_webapp if target=='detail' else (base+'/go.html'+('?page=me' if target=='me' else '') if base else '')
        else:url=album if target=='{album}' else detail_url if target=='{detail}' else target
        url=safe_url(url)
        if not url:continue
        if not rows or item.get('new_row') or len(rows[-1])>=LIMITS[context]['columns']:rows.append([])
        rows[-1].append({'type':kind,'label':item['label'],'url':url})
    return rows


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=b['label'],**({'web_app':WebAppInfo(url=b['url'])} if b['type']=='webapp' else {'url':b['url']})) for b in row] for row in rows]) if rows else None
