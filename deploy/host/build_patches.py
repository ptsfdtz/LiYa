"""Build reviewable patches for exactly the inspected MaiBot 1.3.19 sources.

This command writes only to --output; it never modifies the running source tree.
"""
import argparse
import hashlib
import json
from pathlib import Path

ORIGINALS = {
    'src/plugin_runtime/capabilities/registry.py': '0e54d499e93e85337efe70ab9f69ae33c92bf04f2b74c715d2fef1cf28c477d6',
    'src/A_memorix/core/runtime/services/profile_admin_service.py': '3b092ede44805a4caa124e5e46fc5f0ed137146b0d0cbe034f3d0550447d36fc',
    'src/maisaka/memory/person_profile.py': '570b85f14ab89c128ac389728d174e5126deec2744c30bfa35b5680cbe46ef58',
    'src/maisaka/builtin_tool/query_person_profile.py': '29fcda7e9f1797ca9387759df5bac8ffae7212f97736574a4938d4ea7453975e',
    'src/plugin_runtime/component_query.py': 'a2cd90eadc2a5d9fae0d7626688992be1e91c4f802689363a591e19bb2a3d9d2',
    'src/chat/debounce/controller.py': '1f21013d9dd779e34e6c398b792f87eb833f57f4df1d54416f81c45df968a9fb',
    'src/maisaka/builtin_tool/reply.py': '3a78fb8756f027163f9b83b91a60519f7eba8c7ef1f4d2c724a4cfdbd7216251',
    'src/maisaka/runtime.py': '000e4b62eabc98dc39a59f7ada0d7b5a2b1e103dd7d174ef228fe6e42f76fe4b',
}
PATCHED = {
    'src/plugin_runtime/capabilities/registry.py': 'd3911b0e049f2e48a6384088acc733329e0717501a15a26236d28ff9c755446c',
    'src/A_memorix/core/runtime/services/profile_admin_service.py': '7b1a0713ec49ea29cd099b3884c34e1b75250010ceadde12497e33392f75b2a2',
    'src/maisaka/memory/person_profile.py': '9114253f639950787aa58d06344b9c0ffa01c2548d8944fae83196b60906afce',
    'src/maisaka/builtin_tool/query_person_profile.py': '736151387007089c547334598fed443643811c03e3d88fb33a0cdde137309503',
    'src/plugin_runtime/component_query.py': '166d44f79409b8c962ded34ed5e3e9651a83d4313af9bb93ca06c5ee2f8abcb6',
    'src/chat/debounce/controller.py': '3181c820ab1cc6e3886682fc716baf19d3f8ca8ef59f2717b7dbed96d160bdc6',
    'src/maisaka/builtin_tool/reply.py': 'c3b7478da510de3f44ca86912148238117e98faf8caa0200e1ae8edb0be8f134',
    'src/maisaka/runtime.py': '002381843ff34ddd347cf8db90dded7299d14e774d971212a532887a9cc75532',
}


def replace(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError('Patch anchor differs from inspected version: '+old[:70])
    return text.replace(old, new)


def build(source_root, output):
    hashes = {}
    for name, expected in ORIGINALS.items():
        raw = (source_root/name).read_bytes()
        digest=hashlib.sha256(raw).hexdigest()
        if digest == PATCHED[name]:
            target=output/name
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(raw)
            hashes[name]={'original':expected,'patched':digest}
            continue
        if name.endswith('debounce/controller.py') and digest == 'b12afb458f45d29840cfbaf90ca0a4226c76fdc4ab0dad19569a42da893c2a86':
            raw = raw.replace(b'MENTION_LIMIT = 4.0', b'MENTION_LIMIT = 1.0')
            raw = raw.replace('8 秒/4 秒上限'.encode(), '8 秒/1 秒上限'.encode())
            raw = raw.replace('受@或提及 4 秒上限限制'.encode(), '受@或提及 1 秒上限限制'.encode())
            digest = hashlib.sha256(raw).hexdigest()
        if digest != expected:
            raise ValueError('Uninspected Host source; refusing to patch '+name)
        text = raw.decode('utf-8-sig')
        if name.endswith('capabilities/registry.py'):
            text = replace(text, '    cap_service = supervisor.capability_service\n',
                '    cap_service = supervisor.capability_service\n'
                '    from .liya_memory import handle as liya_memory_handle\n'
                '    cap_service.register_capability("liya.memory", liya_memory_handle)\n')
        elif name.endswith('services/profile_admin_service.py'):
            text = replace(text, '        del chat_id\n        await self.initialize()\n',
                '        await self.initialize()\n'
                '        if chat_id:\n'
                '            from src.plugin_runtime.capabilities.liya_memory import scoped_profile\n'
                '            return await asyncio.to_thread(scoped_profile, self, person_id, chat_id, limit)\n')
            text = replace(text, '        if act == "query":\n',
                '        if act == "query" and str(kwargs.get("chat_id") or "").strip():\n'
                '            from src.plugin_runtime.capabilities.liya_memory import scoped_profile\n'
                '            from src.person_info.person_info import resolve_person_id_for_memory\n'
                '            person_id = str(kwargs.get("person_id") or "").strip()\n'
                '            if not person_id:\n'
                '                person_id = resolve_person_id_for_memory(person_name=str(kwargs.get("person_keyword") or ""))\n'
                '            if not person_id:\n'
                '                return {"success": False, "error": "人物身份不明确"}\n'
                '            return await asyncio.to_thread(scoped_profile, self, person_id, str(kwargs["chat_id"]), int(kwargs.get("limit") or 10))\n'
                '        if act == "query":\n')
        elif name.endswith('memory/person_profile.py'):
            text = replace(text, '                action="query",\n',
                '                action="query",\n                chat_id=anchor_message.session_id,\n')
        elif name.endswith('builtin_tool/query_person_profile.py'):
            text = replace(text, '                action="query",\n',
                '                action="query",\n                chat_id=tool_ctx.runtime.session_id,\n', count=2)
        elif name.endswith('component_query.py'):
            text = replace(text,
                '**{key: value for key, value in context_payload.items() if key not in payload or not payload.get(key)},',
                '**context_payload,')
            text = replace(text,
                '        for key, value in context_payload.items():\n            if key not in payload or not payload.get(key):\n                payload[key] = value\n',
                '        for key, value in context_payload.items():\n            payload[key] = value\n')
        elif name.endswith('debounce/controller.py'):
            text = replace(text, 'MENTION_LIMIT = 1.0', 'MENTION_LIMIT = 4.0')
            text = replace(text, '8 秒/1 秒上限', '8 秒/4 秒上限')
            text = replace(text, '受@或提及 1 秒上限限制', '受@或提及 4 秒上限限制')
            helper = Path(__file__).with_name('private_turn.py').read_text(encoding='utf-8')
            text = replace(text, 'class MessageDebouncer:', helper+'\n\nclass MessageDebouncer:')
            text = replace(text, '        mode: DebounceMode = "natural",\n',
                '        mode: DebounceMode = "natural",\n        is_private: bool = False,\n        text: str = "",\n')
            text = replace(text, '        new_batch = self._first is None\n',
                '        if is_private:\n'
                '            self._first = arrival if self._first is None else self._first\n'
                '            self._urgent = None\n'
                '            wait = private_quiet_seconds(text)\n'
                '            self._deadline = arrival + wait\n'
                '            self.changed.set()\n'
                '            return f"私聊等待本轮说完：静默 {wait:g} 秒；续发重新计时"\n'
                '        new_batch = self._first is None\n')
        elif name.endswith('maisaka/runtime.py'):
            text = replace(text, '            at_bot=message.is_at or message.is_mentioned,\n',
                '            at_bot=message.is_at or message.is_mentioned,\n'
                '            is_private=not self.chat_stream.is_group_session,\n'
                '            text=message.processed_plain_text,\n')
        elif name.endswith('builtin_tool/reply.py'):
            fallback = ('        from src.plugin_runtime.capabilities.liya_memory import send_reply_failure\n'
                        '        return await send_reply_failure(tool_ctx, target_message, reply_tool_args)')
            text = replace(text,
                '        return tool_ctx.build_failure_result(\n'
                '            "reply",\n'
                '            f"生成可见回复或补充内容时发生异常：{type(exc).__name__}: {exc}",\n'
                '            metadata={"reply_id": extension_execution.reply_id},\n'
                '        )', fallback)
            text = replace(text,
                '        return tool_ctx.build_failure_result(\n'
                '            "reply",\n'
                '            "生成可见回复失败。",\n'
                '            metadata=reply_metadata,\n'
                '        )', fallback)
        compile(text, name, 'exec')
        target = output/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode())
        hashes[name] = {'original': expected, 'patched': hashlib.sha256(text.encode()).hexdigest()}
    output.mkdir(parents=True, exist_ok=True)
    (output/'patches.json').write_text(json.dumps(hashes, indent=2)+'\n')
    return hashes


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.source_root, args.output), indent=2))
