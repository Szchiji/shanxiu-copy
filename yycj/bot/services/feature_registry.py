"""Metadata over the EXISTING config keys; not a second configuration store."""
FEATURES={
 'welcome':('bot_welcome_enabled','欢迎消息','site','后续普通/start；不影响详情深链及独立管理入口'),
 'approval_notification':('approval_notify_enabled','老师上架成功通知','city','后续审核/续期/媒体通过；不影响续期频道重发'),
 'community':('community_enabled','本城社区外链','city','后续读取；已打开小程序可能缓存10/60秒'),
 'popup':('popup_announcement_enabled','进入弹窗','city','后续进入；已打开会话不重复弹出'),
 'coupons':('coupons_enabled','优惠功能','site','后续业务请求'),
 'coupon_channel':('broadcast_coupons','优惠频道推送','city','后续优惠推送'),
 'review_channel':('broadcast_reviews','精选评价推送','city','后续符合条件的评价'),
 'directory':('channel_directory','按区目录','city','下一次北京时间12:00刷新'),
}


def enabled(site,feature):
    from bot.services.home_feed import DEFAULT_OPS
    key=FEATURES[feature][0]
    return bool(site.get(key,DEFAULT_OPS[key]))


def metadata(site):
    return [{'feature':name,'key':key,'label':label,'scope':scope,'enabled':enabled(site,name),'effective':when} for name,(key,label,scope,when) in FEATURES.items()]
