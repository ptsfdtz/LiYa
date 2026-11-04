"""Personal tool dispatcher; requester identity is resolved from the active snapshot."""
import asyncio


class PersonalService:
    def __init__(self, conversations, context, experiences=None):
        self.store, self.ctx = conversations, context
        self.experiences = experiences

    async def execute(self, *, session, source_id, action, target='', value='', key=''):
        source = self.store.get_message(session, source_id)
        if not source:
            return {'success': False, 'error': '原始请求不存在，请重新提出。'}
        user = source['user']
        key = {'称呼': 'nickname', '昵称': 'nickname', '玩笑': 'humor', '建议': 'advice'}.get(key, key)
        kind = 'personal:'+action+':'+key+':'+target+':'+value
        cached = self.store.cached_operation(session, source_id, kind)
        if cached:
            return cached
        if action == 'preference':
            allowed = {'nickname': ('叫我', '称呼我', '我的昵称'),
                       'humor': ('玩笑', '调侃', '吐槽'), 'advice': ('建议', '倾诉', '听我说')}
            if key not in allowed or not any(word in source['text'] for word in allowed[key]):
                return {'success': False, 'error': '请明确说出希望怎样称呼或相处。'}
            if key == 'nickname' and value not in source['text']:
                return {'success': False, 'error': '称呼须与本人明确说出的名字一致。'}
            result = {'success': True, 'preferences': self.store.set_preference(session, user, key, value, source_id)}
        elif action == 'topic':
            status={'ongoing':'open','进行中':'open','未完成':'open','已完成':'resolved','完成':'resolved','结束':'dismissed'}.get(target,target) or 'open'
            topics=self.store.get_topics(session,user)
            if not self.store.get_topic(session,user,key):
                matches=[t for t in topics if key in t['key'] or t['key'] in key]
                if len(matches)==1:
                    key=matches[0]['key']
                elif status in ('resolved','dismissed'):
                    return {'success':False,'error':'请明确要更新或结束哪个已有话题。'}
            self.store.topic(session, user, key, value, status, source_id)
            result = {'success': True, 'topics': self.store.get_topics(session, user)}
        elif action == 'forget' and target in ('nickname','humor','advice'):
            if not any(word in source['text'] for word in ('别记','不要记','忘掉','忘记','删除','删掉')):
                return {'success':False,'error':'请先明确要删除哪项个人偏好。'}
            deleted=self.store.forget_preference(session,user,target)
            result={'success':deleted,'message':'已删除当前聊天中的这项相处偏好。' if deleted else '没有这项已保存偏好。'}
        elif action in ('correct','forget') and target.startswith('ep_') and self.experiences:
            result = self.experiences.modify(session,source_id,action,target,value)
        elif action in ('list', 'remember', 'correct', 'forget'):
            try:
                async with asyncio.timeout(50):
                    result = await self.ctx.call_capability('liya.memory', timeout_ms=48000,
                        session=session, source_id=source_id, action=action, target=target, value=value)
            except (RuntimeError, TimeoutError, PermissionError, OSError):
                if action == 'list' and self.experiences:
                    experiences = self.experiences.list(session,user)
                    return {'success': bool(experiences), 'facts_available': False,
                            'experiences': experiences,
                            'warning': '原生个人事实暂不可查询；仅返回共同经历。'}
                return {'success': False, 'error': '个人记忆暂时无法处理，请稍后重试；没有确认操作成功。'}
            if action == 'list' and self.experiences:
                experiences = self.experiences.list(session,user)
                result = {**result, 'facts_available': bool(result.get('success')),
                          'experiences': experiences, 'success': bool(result.get('success') or experiences)}
            if action == 'list' and result.get('success'):
                result = {**result, 'preferences': self.store.get_preferences(session, user),
                          'topics': self.store.get_topics(session, user)}
        else:
            return {'success': False, 'error': '不支持的个人记录操作。'}
        if result.get('success') and action != 'list':
            self.store.save_operation(session, source_id, kind, result)
        return result
