"""Real delivery adapter with a fake bot; no external network."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from aiogram.exceptions import TelegramBadRequest,TelegramForbiddenError
from aiogram.methods import SendMessage,EditMessageText
from bot.services import edit_delivery as delivery,broadcast,extras_store,listing_notify


def setup(monkeypatch,items=None):
    import bot.main as main
    bot=SimpleNamespace(**{k:AsyncMock(return_value=SimpleNamespace(message_id=900)) for k in ['send_message','send_photo','send_video','edit_message_caption','edit_message_text']},send_media_group=AsyncMock(return_value=[SimpleNamespace(message_id=900),SimpleNamespace(message_id=901)]))
    monkeypatch.setattr(main,'bot',bot)
    caption=AsyncMock(return_value=('@newcity','current city template + text links','testbot',None))
    monkeypatch.setattr(broadcast,'_caption',caption)
    monkeypatch.setattr(broadcast,'_media_items',lambda _:items or [])
    refs=[('@old',99)]
    async def remember(lid,chat,mid,**kwargs):refs.append((chat,mid))
    monkeypatch.setattr(extras_store,'load_broadcast',AsyncMock(side_effect=lambda _:refs[-1]))
    monkeypatch.setattr(extras_store,'remember_broadcast',AsyncMock(side_effect=remember))
    return bot,refs,{'lamp_id':'lamp1','user_id':77,'city':'深圳','title':'较新文字','status':'active','extras':{}}


@pytest.mark.parametrize('media',[[],[('image','photo')],[('video','video')],[('image','one'),('video','two')]])
def test_explicit_repost_ignores_existing_ref_tracks_new_and_no_deletion(monkeypatch,media):
    bot,refs,lamp=setup(monkeypatch,media)
    result=asyncio.run(delivery.channel(lamp,'repost'))
    assert result['status']=='synced' and refs==[('@old',99),('@newcity',900)] and result['album']=='https://t.me/newcity/900'
    assert sum(getattr(bot,k).await_count for k in ['send_message','send_photo','send_video','send_media_group'])==1
    for k in ['send_message','send_photo','send_video','send_media_group']:
        if getattr(bot,k).called:assert 'reply_markup' not in getattr(bot,k).call_args.kwargs


def test_text_edits_existing_ref_no_post_and_not_modified_success(monkeypatch):
    bot,refs,lamp=setup(monkeypatch)
    bot.edit_message_caption.side_effect=TelegramBadRequest(method=EditMessageText(chat_id=1,message_id=99,text='x'),message='message is not modified')
    result=asyncio.run(delivery.channel(lamp,'text'))
    assert result['status']=='synced' and bot.edit_message_caption.call_args.kwargs['chat_id']=='@old'
    bot.send_message.assert_not_called();extras_store.remember_broadcast.assert_not_called()
    bot.edit_message_text.assert_not_called()


@pytest.mark.parametrize('kind,expected,retryable',[('rejected','failed',True),('timeout','unknown',False),('refs','unknown',False)])
def test_repost_failures_and_unknown_never_mark_success(monkeypatch,kind,expected,retryable):
    bot,refs,lamp=setup(monkeypatch)
    if kind=='rejected':bot.send_message.side_effect=TelegramForbiddenError(method=SendMessage(chat_id=1,text='x'),message='Forbidden')
    elif kind=='timeout':bot.send_message.side_effect=TimeoutError('simulated ambiguous delivery')
    else:extras_store.remember_broadcast.side_effect=RuntimeError('simulated reference write failure after acknowledgement')
    result=asyncio.run(delivery.channel(lamp,'repost'))
    assert result['status']==expected and result['retryable']==retryable
    assert len(refs)==1


def test_notification_adapter_passes_current_owner_city_new_album_lamp_id(monkeypatch):
    _,_,lamp=setup(monkeypatch)
    send=AsyncMock(return_value=True);monkeypatch.setattr(listing_notify,'notify_publisher_approved',send)
    assert asyncio.run(delivery.notify(lamp,'https://t.me/newcity/900'))['status']=='sent'
    assert send.call_args.args==(77,'较新文字','https://t.me/newcity/900')
    assert send.call_args.kwargs=={'city':'深圳','lamp_id':'lamp1','raise_errors':True,'listing_active':True,'operation_id':None,'actor_id':None}


def test_rejection_notification_and_strict_failure_outcomes(monkeypatch):
    bot,_,lamp=setup(monkeypatch)
    asyncio.run(delivery.notify(lamp,None,rejected=True));assert bot.send_message.call_args.args==(77,'你的灯笼投稿未通过审核。')
    bot.send_message.side_effect=TelegramForbiddenError(method=SendMessage(chat_id=1,text='x'),message='Forbidden')
    assert asyncio.run(delivery.notify(lamp,None,rejected=True))['status']=='failed'
    bot.send_message.side_effect=TimeoutError()
    assert asyncio.run(delivery.notify(lamp,None,rejected=True))['status']=='unknown'
