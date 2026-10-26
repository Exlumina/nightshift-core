"""Deterministic, side-effect-free Overnight Shift admission and ordering.

The caller supplies fresh task state and independently measured capacity.
``ready`` is a non-conflicting simultaneous dispatch batch, ``ranked`` is the
ordered individually eligible pool. Neither list changes the input records.
"""
from datetime import datetime, timezone
import math
import os
import re
import time
from collections import Counter


_DONE = {'done', 'completed'}
_READY = {'todo', 'in progress'}
_TERMINAL = _DONE | {'canceled', 'cancelled', 'duplicate', 'archived'}


def _number(value, *, required=False):
    if value is None and not required:
        return 0.0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('expected nonnegative finite number')
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError('expected nonnegative finite number')
    return value


def _count(value):
    number = _number(value, required=True)
    if not number.is_integer():
        raise ValueError('slot counts must be integers')
    return int(number)


def _timestamp(value):
    if value is None:
        return None
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('timestamp requires timezone')
        return parsed.timestamp()
    return _number(value, required=True)


def _status(row):
    status = row.get('status', '')
    if isinstance(status, dict):
        kind = str(status.get('type', '')).lower()
        if kind == 'completed':
            return 'done'
        if kind in ('canceled', 'cancelled'):
            return 'canceled'
        status = status.get('name', '')
    return str(status).strip().lower()


def _priority(row):
    value = row.get('triaged_priority', row.get('priority', 0))
    if isinstance(value, dict):
        value = value.get('value', 0)
    return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 4 else 5


def _target_conflict(targets, occupied):
    for left in targets:
        left = os.path.normpath(left)
        for right in occupied:
            right = os.path.normpath(right)
            if (left == right or left.startswith(right.rstrip('/') + '/')
                    or right.startswith(left.rstrip('/') + '/')):
                return True
    return False



def human_requirement_reason(row):
    """Exclude explicit human dependencies before planning or admission.

    Trusted project registration still supplies positive authorization. This
    additional veto recognizes issue metadata and direct human-work wording;
    ordinary later acceptance/review does not make autonomous execution human work.
    """
    if row.get('human_blocked') or row.get('blocked_by_human'):
        return 'human_blocked'
    if any(row.get(field) for field in (
            'requires_human', 'human_required', 'requires_human_presence',
            'requires_human_input', 'requires_joint_decision', 'human_in_loop',
            'awaiting_human_input', 'awaiting_human_approval')):
        return 'requires_human'
    if row.get('autonomous') is False or row.get('nightshift_eligible') is False:
        return 'requires_human'
    labels = row.get('labels', [])
    if isinstance(labels, (list, tuple, set)):
        for label in labels:
            name = label.get('name', '') if isinstance(label, dict) else label
            if isinstance(name, str) and re.sub(r'[-_]+', ' ', name.lower()).strip() in (
                    'requires human', 'human required', 'human input', 'human review',
                    'joint decision', 'do together', 'human in the loop'):
                return 'requires_human'
    text = '\n'.join(str(row.get(field) or '') for field in ('title', 'description'))
    patterns = (
        r'\b(?:joint|shared) (?:decision|review|selection)\b',
        r'\b(?:decide|review|select|choose|do|work|complete|discuss)\b[^.\n;]{0,70}\btogether\b',
        r'\b(?:with|together with) (?:a human|the human|the user)\b',
        r'\b(?:requires?|needs?|awaiting|waiting for|pending) (?:a |the |actual |live )?'
        r'(?:human|user)(?:[ -](?:input|presence|connection|approval|decision|review|authentication))?\b',
        r'\b(?:human|user)[ -](?:input|presence|connection|approval|decision|review|authentication)'
        r' (?:is |still )?(?:required|needed|pending)\b',
        r'\b(?:do not|don\'t|must not|cannot|can\'t) (?:run|execute|perform|start|do)'
        r'[^.\n;]{0,50}\b(?:autonomously|unattended|without (?:a human|the user))\b',
    )
    for clause in re.split(r'[.\n;]', text.lower()):
        # An explicit absence of a human dependency is not a human hold.
        clause = re.sub(r'\b(?:no|without) (?:human|user)[ -](?:input|presence|connection|approval|review)'
                        r'(?: (?:is )?(?:required|needed))?\b', '', clause)
        if any(re.search(pattern, clause) for pattern in patterns):
            return 'requires_human'
    return None


def schedule(issues, *, remaining_seconds, capabilities, active_targets=None,
             resources=None, allow_backlog=False, now=None):
    """Rank and admit current work without mutating issues or external state.

    Issue authority and ownership fail closed: ``authorized`` and
    ``owner_available`` must explicitly be True. ``estimate_seconds`` is the
    conservative upper estimate, added to verification and stopping time.
    ``dependencies`` contains issue IDs, including supplied Done records.
    ``explicit_order`` is an optional nonnegative user-direction rank.
    ``due_at`` / ``ready_since`` accept epoch seconds or timezone-aware ISO.

    Capacity keys: max_parallel (default 2), max_heavy (1), active_jobs (0),
    active_heavy (0), heavy_allowed (True), memory_pressure ('normal'), and
    optional available_memory_bytes / available_disk_bytes. Byte availability
    is free capacity after the caller's reserve, not total machine capacity.
    """
    remaining = _number(remaining_seconds, required=True)
    now = time.time() if now is None else _number(now, required=True)
    capabilities = set(capabilities or ())
    resources = dict(resources or {})
    occupied = set(active_targets or ())
    counts = Counter(r.get('id') for r in issues
                     if isinstance(r, dict) and isinstance(r.get('id'), str))
    excluded, deferred = [], []
    by_id, metadata = {}, {}

    def exclude(key, reason):
        excluded.append({'id': key, 'reason': reason})

    # Full lookup retains completed dependencies; duplicate identities are never
    # silently collapsed into a job that could be dispatched twice.
    for row in issues:
        if not isinstance(row, dict):
            exclude(None, 'invalid_issue')
            continue
        key = row.get('id')
        if not isinstance(key, str) or not key.strip():
            exclude(key, 'invalid_id')
            continue
        if counts[key] != 1:
            exclude(key, 'duplicate_id')
            continue
        by_id[key] = row

    for key, row in by_id.items():
        status = _status(row)
        if row.get('archivedAt') or row.get('archived') or status in _TERMINAL:
            exclude(key, 'terminal_status')
            continue
        if status not in _READY and status != 'backlog':
            exclude(key, 'status_not_ready')
            continue
        if row.get('authorized') is not True:
            exclude(key, 'not_authorized')
            continue
        if row.get('owner_available') is not True or row.get('claimed_by_other'):
            exclude(key, 'owner_unavailable')
            continue
        human_reason = human_requirement_reason(row)
        if human_reason:
            exclude(key, human_reason)
            continue
        dependencies = row.get('dependencies', [])
        required = row.get('capabilities', [])
        targets = row.get('targets', [])
        if (row.get('resource_class', 'light') not in ('light', 'heavy')
                or not isinstance(dependencies, (list, tuple))
                or not all(isinstance(d, str) and d for d in dependencies)
                or not isinstance(required, (list, tuple, set))
                or not all(isinstance(c, str) and c for c in required)
                or not isinstance(targets, (list, tuple, set))
                or not all(isinstance(t, str) and t for t in targets)):
            exclude(key, 'invalid_contract')
            continue
        missing = set(required) - capabilities
        if missing:
            exclude(key, 'missing_capability:' + ','.join(sorted(missing)))
            continue
        try:
            cost = sum((_number(row.get('estimate_seconds'), required=True),
                        _number(row.get('verify_seconds')), _number(row.get('stop_seconds'))))
            memory = _number(row.get('memory_bytes'))
            disk = _number(row.get('disk_bytes'))
            explicit = _number(row.get('explicit_order')) if row.get('explicit_order') is not None else math.inf
        except ValueError:
            exclude(key, 'invalid_budget')
            continue
        try:
            due = _timestamp(row.get('due_at'))
            age = _timestamp(row.get('ready_since', row.get('created_at')))
        except (ValueError, TypeError, OverflowError):
            exclude(key, 'invalid_timestamp')
            continue
        metadata[key] = dict(cost=cost, due=due, age=age if age is not None else now,
                             explicit=explicit, memory=memory, disk=disk,
                             dependencies=list(dependencies), priority=_priority(row),
                             backlog=status == 'backlog')

    # Find cycles on the entire supplied incomplete dependency graph, so a
    # cycle hidden behind an otherwise unavailable issue still cannot run.
    cycles, visited, stack, on_stack = set(), set(), [], set()

    def visit(key):
        if key in on_stack:
            cycles.update(stack[stack.index(key):])
            return
        if key in visited or key not in by_id or _status(by_id[key]) in _DONE:
            return
        visited.add(key)
        stack.append(key)
        on_stack.add(key)
        deps = by_id[key].get('dependencies', [])
        if isinstance(deps, (list, tuple)):
            for dependency in deps:
                if isinstance(dependency, str):
                    visit(dependency)
        on_stack.remove(key)
        stack.pop()

    for key in by_id:
        visit(key)

    # Bounded fixed point propagates urgency backwards without changing Linear.
    effective = {key: data['priority'] for key, data in metadata.items()}
    effective_explicit = {key: data['explicit'] for key, data in metadata.items()}
    effective_due = {key: data['due'] if data['due'] is not None else math.inf
                     for key, data in metadata.items()}
    for _ in range(len(metadata)):
        changed = False
        for key, data in metadata.items():
            for dep in data['dependencies']:
                if dep not in effective:
                    continue
                for rank in (effective, effective_explicit, effective_due):
                    if rank[dep] > rank[key]:
                        rank[dep] = rank[key]
                        changed = True
        if not changed:
            break

    eligible = []
    for key, data in metadata.items():
        if key in cycles:
            exclude(key, 'dependency_cycle')
            continue
        if data['cost'] > remaining:
            exclude(key, 'insufficient_time')
            continue
        due = effective_due[key]
        if now < due < now + data['cost']:
            exclude(key, 'deadline_infeasible')
            continue
        dependency_problem = None
        for dep in data['dependencies']:
            if dep not in by_id:
                dependency_problem = 'missing_dependency:' + dep
                break
            if _status(by_id[dep]) not in _DONE:
                dependency_problem = 'waiting_dependency:' + dep
                break
        if dependency_problem:
            exclude(key, dependency_problem)
            continue
        if effective[key] == 5 and not math.isfinite(effective_explicit[key]):
            exclude(key, 'needs_priority_triage')
            continue
        if data['backlog'] and not allow_backlog:
            exclude(key, 'backlog_not_enabled')
            continue
        eligible.append(key)

    # Backlog is a fallback pool, never extra filler beside a ready Todo issue.
    has_ready = any(not metadata[key]['backlog'] for key in eligible)
    pool = []
    for key in eligible:
        if has_ready and metadata[key]['backlog']:
            deferred.append({'id': key, 'reason': 'ready_queue_preferred'})
        else:
            pool.append(key)

    def sort_key(key):
        data = metadata[key]
        near_due = effective_due[key] <= now + remaining
        return (effective_explicit[key], 0 if near_due else 1,
                effective_due[key] if near_due else math.inf, effective[key], data['age'], key)

    pool.sort(key=sort_key)
    ready = []
    parallel = _count(resources.get('max_parallel', 2))
    heavy_limit = _count(resources.get('max_heavy', 1))
    active = _count(resources.get('active_jobs', 0))
    heavy = _count(resources.get('active_heavy', 0))
    available_memory = (_number(resources['available_memory_bytes'], required=True)
                        if resources.get('available_memory_bytes') is not None else math.inf)
    available_disk = (_number(resources['available_disk_bytes'], required=True)
                      if resources.get('available_disk_bytes') is not None else math.inf)
    pressure = str(resources.get('memory_pressure', 'normal')).lower()
    heavy_ok = resources.get('heavy_allowed', True) is True and pressure in ('normal', '1', 'none', 'nominal')
    for key in pool:
        row, data = by_id[key], metadata[key]
        is_heavy = row.get('resource_class', 'light') == 'heavy'
        # Public core never bypasses pressure policy using workload-specific receipts.
        bounded_ok = False
        reason = None
        if active + len(ready) >= parallel:
            reason = 'parallel_capacity'
        elif _target_conflict(row.get('targets', []), occupied):
            reason = 'target_conflict'
        elif is_heavy and not (heavy_ok or bounded_ok):
            reason = 'resource_pressure'
        elif is_heavy and heavy >= heavy_limit:
            reason = 'heavy_capacity'
        elif data['memory'] > available_memory:
            reason = 'memory_capacity'
        elif data['disk'] > available_disk:
            reason = 'disk_capacity'
        if reason:
            deferred.append({'id': key, 'reason': reason})
            continue
        ready.append(row)
        occupied.update(row.get('targets', []))
        heavy += int(is_heavy)
        available_memory -= data['memory']
        available_disk -= data['disk']

    return {'ready': ready, 'ranked': [by_id[key] for key in pool],
            'excluded': excluded, 'deferred': deferred,
            'effective_priority': effective}
