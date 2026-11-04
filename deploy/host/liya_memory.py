"""Host capability for verified, author-owned, chat-scoped personal memories.

Installed beside the pinned Host capability registry, never imported by Runner.
"""
import asyncio
import hashlib
import json
import re
import time


def metadata(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def personal_paragraphs(kernel, person_id, chat_id):
    """Exclude mixed-person and unscoped sources even if vector search returns them."""
    store = kernel.metadata_store
    rows = store.query('''SELECT hash,content,metadata FROM paragraphs
        WHERE (is_deleted IS NULL OR is_deleted=0)
          AND json_extract(CASE WHEN json_valid(metadata) THEN metadata ELSE '{}' END,'$.chat_id')=?
        ORDER BY created_at DESC''', (chat_id,))
    found = []
    for row in rows:
        meta = metadata(row.get('metadata'))
        scopes = set(meta.get('chat_ids') or []) | {str(meta.get('chat_id') or '')}
        if scopes != {chat_id}:
            continue
        if set(meta.get('person_ids') or []) != {person_id} or meta.get('source_type') != 'person_fact':
            continue
        paragraph = store.get_paragraph(row['hash'])
        projected = store.project_fact_paragraph(paragraph)
        if projected:
            found.append({'id': row['hash'], 'text': str(meta.get('liya_value') or projected.get('content') or ''),
                          'source_ids': meta.get('evidence_message_ids') or []})
    return found


def scoped_profile(kernel, person_id, chat_id, limit=10):
    from src.A_memorix.core.utils.profile_text import build_structured_profile_text
    facts = personal_paragraphs(kernel, person_id, chat_id)[:max(1, min(100, limit))]
    profile_text = build_structured_profile_text(person_id=person_id, stable_facts=[f['text'] for f in facts])
    return {'success': True, 'summary': '\n'.join(f['text'] for f in facts), 'profile_text': profile_text,
            'traits': [f['text'] for f in facts], 'evidence': facts,
            'person_id': person_id, 'person_name': '', 'profile_source': 'chat_scoped_facts',
            'has_manual_override': False}


async def operate(kernel, *, message, session, action, target='', value=''):
    """The caller supplies a Host-resolved message, never a model-supplied person ID."""
    from src.person_info.person_info import resolve_person_id_for_memory
    user = message.message_info.user_info.user_id
    person = resolve_person_id_for_memory(platform=message.platform, user_id=user)
    if not person:
        return {'success': False, 'error': '无法确认当前成员身份。'}
    await kernel.initialize()
    facts = await asyncio.to_thread(personal_paragraphs, kernel, person, session)
    if action == 'list':
        return {'success': True, 'facts': facts[:100], 'count': len(facts),
                'scope': '仅当前聊天中你的个人事实，不包含群聊公共摘要或其他聊天。'}
    text = str(message.processed_plain_text or '')
    if action not in ('remember', 'correct', 'forget'):
        return {'success': False, 'error': '不支持的记忆操作。'}
    if action == 'remember' and not any(word in text for word in ('记住', '记一下', '记着', '记下')):
        return {'success': False, 'error': '请先明确表示希望记住什么。'}
    if action == 'correct' and not any(word in text for word in ('记错', '更正', '纠正', '不对', '改成', '不是', '现在')):
        return {'success': False, 'error': '请明确指出需要更正的事实和正确内容。'}
    if action == 'forget' and not any(word in text for word in ('别记', '不要记', '忘掉', '忘记', '删除', '删掉')):
        return {'success': False, 'error': '请先明确表示要删除哪条记忆。'}
    selected = None
    if action in ('correct', 'forget'):
        if len(target.strip()) < 2:
            return {'success': False, 'error': '请具体指出哪条记忆；“这件事”不能直接用作删除范围。'}
        matches = [fact for fact in facts if fact['id'] == target or target in fact['text']]
        if len(matches) != 1:
            return {'success': False, 'error': '没有唯一匹配的本人记忆，请先查询并明确具体条目。'}
        selected = matches[0]
    if action in ('remember', 'correct'):
        if not value.strip() or len(value) > 400:
            return {'success': False, 'error': '请提供不超过400字的明确个人事实。'}
        normalized = lambda v: re.sub(r'[\s，,。！!：:；;]', '', v)
        if normalized(value) not in normalized(text) or not any(word in value for word in ('我', '本人')):
            return {'success': False, 'error': '个人事实须采用本人当前请求中明确说出的原话，不能记入转述或推测。'}
        # Separate paragraph identities by chat even when the same fact appears elsewhere.
        token = hashlib.sha256((session+'\0'+message.message_id+'\0'+action+'\0'+value).encode()).hexdigest()
        scope_token = hashlib.sha256((session+'\0'+person).encode()).hexdigest()[:20]
        result = await kernel.ingest_text(
            external_id='liya-personal:'+token, source_type='person_fact',
            text=value.strip()+'\n〔个人记忆范围：'+scope_token+'〕',
            chat_id=session, person_ids=[person], timestamp=time.time(), user_id=user,
            group_id=str(getattr(message.message_info.group_info, 'group_id', '') or ''),
            metadata={'evidence_source': 'user_supported', 'writeback_source': 'liya_personal', 'liya_value': value.strip(),
                      'evidence_message_ids': [message.message_id], 'platform': message.platform,
                      'fact_claim': {'fact_key': 'liya:'+session+':'+token, 'trust': 'manual_confirmed'}})
        if not result.get('success', bool(result.get('stored_ids'))) or not (result.get('stored_ids') or result.get('skipped_ids')):
            return {'success': False, 'error': '记忆未写入，请稍后重试。'}
    if selected:
        result = await kernel.memory_delete_admin(action='execute', mode='paragraph',
            selector={'hashes': [selected['id']]}, requested_by='liya:'+user, reason='user_requested_'+action)
        if not result.get('success'):
            return {'success': False, 'error': '旧记忆尚未成功移除，请稍后检查；不能确认更正完成。'}
        remaining = await asyncio.to_thread(personal_paragraphs, kernel, person, session)
        if any(f['id'] == selected['id'] for f in remaining):
            return {'success': False, 'error': '旧记忆仍可查询，操作未完成。'}
    return {'success': True, 'action': action, 'message': '已删除该条个人记忆。' if action == 'forget' else '已保存当前聊天中的个人事实。'}


async def handle(plugin_id, capability, args):
    if plugin_id != 'local.liya-companion' or capability != 'liya.memory':
        raise PermissionError('This capability belongs to Liya companion only')
    from src.services import message_service
    from src.A_memorix.runtime_registry import get_runtime_kernel
    session, source = str(args.get('session') or ''), str(args.get('source_id') or '')
    if not session or not source:
        return {'success': False, 'error': '缺少真实聊天来源。'}
    message = await asyncio.to_thread(message_service.get_message_by_id, message_id=source, chat_id=session)
    if message is None or message.session_id != session:
        return {'success': False, 'error': '找不到当前聊天中的原始请求。'}
    from src.common.runtime_loop import run_on_main_loop
    async def on_main():
        kernel = get_runtime_kernel()
        if kernel is None:
            return {'success': False, 'error': '长期记忆暂不可用。'}
        async with asyncio.timeout(45):
            return await operate(kernel, message=message, session=session, action=str(args.get('action') or ''),
                                 target=str(args.get('target') or ''), value=str(args.get('value') or ''))
    return await run_on_main_loop(on_main())


async def send_reply_failure(tool_ctx, target_message, reply_tool_args):
    """A model outage is reported through the same guarded, single-body send path."""
    from src.maisaka.builtin_tool.reply import _invoke_before_post_process_hook
    from src.services import send_service
    text = '这次回复没能生成成功，稍后再试一下。'
    text, _ = await _invoke_before_post_process_hook(tool_ctx, text, target_message.message_id,
                                                     {**reply_tool_args, 'liya_generation_failed': True})
    sequences = tool_ctx.post_process_reply_message_sequences(text, skip_post_process=True)
    sent = await send_service._send_to_target_with_message(
        message_sequence=sequences[0], stream_id=tool_ctx.runtime.session_id,
        processed_plain_text=text, set_reply=False, reply_message=target_message,
        typing=False, sync_to_maisaka_history=True, maisaka_source_kind='guided_reply')
    if sent is None:
        return tool_ctx.build_failure_result('reply', '回复生成失败，通知未送达或已被停止。',
                                             metadata={'generation_failed': True})
    return tool_ctx.build_success_result('reply', '回复生成失败，已向用户说明。',
        metadata={'generation_failed': True, 'sent_message_ids': [sent.message_id]})
