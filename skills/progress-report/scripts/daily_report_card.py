"""Render the approved daily-report layout; activity numbers come from raw stats."""
from datetime import datetime, timedelta, timezone
import html
import re


def markdown(content, size='normal'):
    return {'tag': 'markdown', 'content': content, 'text_size': size}


def box(elements, background='default'):
    return {'tag': 'column_set', 'flex_mode': 'none', 'columns': [{
        'tag': 'column', 'width': 'weighted', 'weight': 1,
        'background_style': background, 'padding': '12px',
        'vertical_spacing': '4px', 'elements': elements,
    }]}


def build_card(body, raw):
    parts = re.split(r'^\*\*([^*\n]+)\*\*\s*$', body.strip(), flags=re.MULTILINE)
    summary = parts[0].strip()
    sections = {}
    for name, content in zip(parts[1::2], parts[2::2]):
        if name in sections:
            raise ValueError('Duplicate daily report section: ' + name)
        sections[name] = content.strip()
    members = raw['stats']['by_member'].values()
    allowed = {m['display_name'] for m in members} | {'团队待关注'}
    if not summary or set(sections) - allowed:
        raise ValueError('Expected summary, member sections and optional 团队待关注')
    if any(term in body for term in ('阅读口径：', '北京时间窗口：', '统计附表', '提交审计')):
        raise ValueError('Keep accounting details in the evidence, not the daily card')
    member_elements, inactive = [], []
    for member in members:
        name = member['display_name']
        if name not in sections:
            raise ValueError('Missing member section: ' + name)
        if not member['active_pr_count'] and not member['observed_commit_count']:
            inactive.append(name)
            continue
        content = sections[name]
        lines = [line for line in content.splitlines() if line.strip()]
        if not 1 <= len(lines) <= 3 or any(not line.startswith('- ') for line in lines):
            raise ValueError('Member progress needs 1–3 single-line bullets: ' + name)
        counts = member['prs']
        labels = [('merged_main', '主干合入'), ('merged_branch', '分支合入'), ('ongoing', '期内仍开放')]
        numbers = [f'{label} {counts[key]}' for key, label in labels if counts.get(key)]
        if not numbers and counts.get('closed_unmerged'):
            numbers = [f"关闭未合并 {counts['closed_unmerged']}"]
        elements = [markdown('**' + html.escape(name) + '**')]
        if numbers:
            elements.append(markdown("<font color='grey'>PR · " + ' / '.join(numbers) + '</font>', 'notation'))
        elements.append(markdown(content))
        member_elements.append(box(elements))
    if inactive:
        member_elements.append(markdown("<font color='grey'>" + html.escape('、'.join(inactive)) +
                                         '：本期未采集到代码或 PR 活动。</font>', 'notation'))
    end = datetime.fromisoformat(raw['until'].replace('Z', '+00:00')).astimezone(timezone(timedelta(hours=8)))
    elements = [box([markdown('**今日重点**', 'heading-3'), markdown(summary)], 'blue-50'), {
        'tag': 'collapsible_panel', 'expanded': True,
        'header': {'title': {'tag': 'plain_text', 'content': '成员进展'}},
        'vertical_spacing': '8px', 'elements': member_elements,
    }]
    if sections.get('团队待关注'):
        elements.append(box([markdown('**需关注**'), markdown(sections['团队待关注'])], 'grey-50'))
    return {
        'schema': '2.0',
        'config': {'width_mode': 'default', 'update_multi': True,
                   'summary': {'content': f'{end.month} 月 {end.day} 日团队日报'}},
        'header': {'title': {'tag': 'plain_text', 'content': f'团队日报｜{end.month} 月 {end.day} 日'},
                   'subtitle': {'tag': 'plain_text', 'content': f'截至北京时间 {end:%H:%M} · 最近 24 小时'},
                   'template': 'blue'},
        'body': {'direction': 'vertical', 'padding': '12px', 'vertical_spacing': '12px', 'elements': elements},
    }
