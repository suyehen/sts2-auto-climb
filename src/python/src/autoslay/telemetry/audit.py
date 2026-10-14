"""Bounded Agent lifecycle evidence; recording never controls game execution."""
import re
import json
from pathlib import Path
from uuid import uuid4
from autoslay.archive.repository import atomic, now
from autoslay.telemetry import decisions, runs
from autoslay.archive.collector import store
from autoslay.paths import ROOT

FIELDS = {'analysis_id', 'plan_request_id', 'request_id', 'plan_step', 'status',
          'provider', 'model', 'validation_attempt', 'attempt', 'max_tokens',
          'elapsed_ms', 'usage', 'code', 'reason', 'rule_id', 'decision_basis',
          'steps', 'validation_issues', 'expected_state_version', 'action_id',
          'target_id', 'original_trace_id', 'route', 'retry_reason', 'provider_label'}
USAGE = {'prompt_tokens', 'completion_tokens', 'total_tokens', 'reasoning_tokens',
         'request_attempts', 'validation_attempts'}
EVENT_LABELS = {
    'trace_started': '开始本轮决策', 'trace_finished': '本轮处理结束',
    'analysis_completed': '候选方案分析完成', 'model_requested': '请求模型决策',
    'model_attempt': '模型传输尝试', 'model_call_started': '开始模型调用',
    'model_call_returned': '模型调用已返回（尚需校验）', 'model_call_failed': '模型调用失败',
    'model_call_cancelled': '模型调用已取消', 'plan_validated': '动作计划通过校验',
    'plan_rejected': '动作计划未通过校验', 'action_submitted': '已提交游戏动作',
    'action_receipt': '收到动作回执', 'action_recovery_checked': '查询中断动作的执行状态',
    'action_recovered': '已恢复中断动作的回执', 'local_requested': '请求本地策略执行',
    'local_returned': '本地策略已返回', 'agent_error': 'Agent 异常停止',
    'control_started': '人工启动 Agent', 'control_paused': '暂停 Agent',
    'provider_switched': '人工切换决策后端', 'request_retrying': '请求有限重试',
    'request_cancelled': '请求已取消', 'request_not_executed': '决策未执行',
    'request_state_rebound': '决策已重新绑定到最新状态版本',
}
ACTION_LABELS = {'play_card': '打出卡牌', 'end_turn': '结束回合', 'use_potion': '使用药水',
    'choose_reward_card': '选择奖励卡牌', 'claim_reward': '领取奖励', 'proceed_rewards': '离开奖励房间',
    'choose_event_option': '选择事件选项', 'choose_map_node': '前往地图房间',
    'skip_card_reward': '跳过抓牌', 'confirm_selection': '确认选牌', 'toggle_selection_card': '选择或取消选择卡牌',
    'discard_potion': '丢弃药水', 'choose_rest_option': '选择休息处选项', 'proceed_rest': '离开休息处',
    'buy_shop_item': '购买商品', 'open_shop': '打开商店', 'close_shop': '关闭商店', 'proceed_shop': '离开商店',
    'confirm_card_selection': '确认选牌', 'cancel_card_selection': '取消选牌',
    'confirm_combat_selection': '确认战斗选牌', 'skip_combat_choice': '跳过战斗选择'}


def action_labels(snapshot, step):
    """Capture names at event time; missing evidence stays explicitly unknown."""
    state = snapshot.get('state') or {}
    action_id, target_id = step.get('action_id'), step.get('target_id')
    action = next((a for a in state.get('legal_actions', []) if a.get('action_id') == action_id), {})
    name = action.get('name') or action.get('label') or ACTION_LABELS.get(action.get('type') or action_id)
    for card in (state.get('player') or {}).get('hand', []):
        if action_id == 'play_'+str(card.get('instance_id')):
            name = '打出「'+(card.get('name') or '未记录牌名')+'」'
    for potion in (state.get('player') or {}).get('potions') or []:
        for field, verb in [('use_action_id', '使用'), ('discard_action_id', '丢弃')]:
            if action_id and potion.get(field) == action_id:
                name = verb+'「'+(potion.get('name') or '未记录药水名称')+'」'
    for node in (state.get('map') or {}).get('nodes') or []:
        if node.get('action_id') == action_id:
            coordinate = node.get('coordinate') or {}
            room = {'Monster':'普通战斗', 'Elite':'精英战斗', 'Boss':'首领', 'RestSite':'休息处',
                    'Shop':'商店', 'Treasure':'宝箱', 'Unknown':'未知房间', 'Ancient':'远古之民'}.get(node.get('type'), '未记录房间类型')
            name = f"前往{room}（列 {coordinate.get('column', '未知')}，行 {coordinate.get('row', '未知')}）"
    for item in (state.get('shop') or {}).get('items') or []:
        if item.get('action_id') == action_id:
            info = item.get('details') or {}
            title = (info.get('card') or {}).get('name') or info.get('name')
            name = '购买「'+title+'」' if title else '购买商品（未记录商品名称）'
            if item.get('type') == 'MerchantCardRemovalEntry': name = '购买删牌服务'
    for screen, collection in [('event_screen', 'options'), ('reward_screen', 'cards'),
                               ('card_selection', 'cards'), ('rest_site', 'options')]:
        for option in (state.get(screen) or {}).get(collection) or []:
            if option.get('action_id') == action_id:
                card = option.get('card') or {}
                title = option.get('title') or option.get('text') or card.get('name') or option.get('name')
                if title:
                    name = '选择「'+str(title)[:500]+'」'
    for reward in (state.get('reward_screen') or {}).get('rewards') or []:
        if reward.get('action_id') == action_id:
            name = '打开卡牌奖励' if reward.get('type') == 'CardReward' else '领取奖励：'+str(reward.get('description') or '未记录奖励名称')[:500]
    target = next((e for e in state.get('enemies', []) if e.get('instance_id') == target_id), {}) if target_id else {}
    return dict(action_id=action_id, action_label=str(name or '未知动作（当时未记录释义）')[:500],
                target_id=target_id, target_label=target.get('name') or ('无目标' if target_id is None else '未知目标（当时未记录名称）'))


def safe_usage(value):
    return {k: v for k, v in (value or {}).items()
            if k in USAGE and type(v) is int and v >= 0} if isinstance(value, dict) else {}


def error_code(error):
    code = str(error)
    return code if re.fullmatch(r'[A-Z_0-9]{1,80}', code) else 'AGENT_ERROR'


class Audit:
    def __init__(self, directory, archive=True, sink=store, pending=None):
        self.directory = Path(directory)
        self.archive, self.sink = archive, sink
        self.pending = Path(pending) if pending else ROOT/'data/runs/.pending'
        self.health = dict(last_success_at=None, write_failures=0, last_error=None)
        try:
            previous = json.loads((self.directory/'health.json').read_text(encoding='utf-8'))
            self.health.update({k: previous[k] for k in self.health if k in previous})
        except (OSError, ValueError, TypeError):
            pass

    def status(self):
        result = dict(self.health, archive_enabled=self.archive)
        try:
            result['pending_count'] = len(list(self.pending.glob('*.json'))) if self.archive else 0
        except OSError:
            result['pending_count'] = None
        result['degraded'] = bool(result['last_error'] or result['pending_count'])
        return result

    def emit(self, kind, snapshot=None, trace_id=None, location=None, **details):
        # Provider response bodies, headers, endpoint URLs and keys never enter this API.
        selected = {k: v for k, v in details.items() if k in FIELDS}
        if 'usage' in selected:
            selected['usage'] = safe_usage(selected['usage'])
        snapshot = snapshot or {}
        if selected.get('action_id'):
            selected.update(action_labels(snapshot, selected))
        if selected.get('steps'):
            selected['step_labels'] = [action_labels(snapshot, step) for step in selected['steps']]
        state = snapshot.get('state') or {}
        event = dict(event_id=uuid4().hex, kind='agent_audit', recorded_at=now(),
                     location=location or runs.location(snapshot), snapshot=decisions.public_snapshot(snapshot),
                     details=dict(schema_version='agent-audit/1.0', event=kind,
                                  event_label=EVENT_LABELS.get(kind, '未登记释义的审计事件'), trace_id=trace_id,
                                  session_epoch=snapshot.get('session_epoch'), run_id=state.get('run_id'),
                                  state_version=snapshot.get('state_version'), **selected))
        try:
            # Full snapshots use the archive's content-addressed storage, not a
            # second copy for every transport and validation event.
            atomic(self.directory/'events'/(event['event_id']+'.json'),
                   {k: v for k, v in event.items() if k != 'snapshot'})
            if self.archive and event['location'].get('run_key'):
                pointer = self.sink(event)
                if not pointer.get('recorded'):
                    raise OSError('Archive evidence pending')
            self.health.update(last_success_at=event['recorded_at'], last_error=None)
        except Exception:
            self.health['write_failures'] += 1
            self.health['last_error'] = 'AUDIT_WRITE_FAILED'
        try:
            atomic(self.directory/'health.json', self.health)
        except Exception:
            self.health['last_error'] = 'AUDIT_WRITE_FAILED'
        return event['event_id']
