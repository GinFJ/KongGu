"""Complete missing metadata only within a unique, compatible source pair."""
from collections import defaultdict

from core.identity import identity_from_filename
from core.models import MemberIdentity


def complete_pair_identities(blocks, sources):
    by_source = defaultdict(list)
    for block in blocks:
        by_source[str(block.get('source_file') or '')].append(block)
    by_name = defaultdict(list)
    for source in sources:
        rows = by_source.get(source.file_name) or by_source.get(source.source_path) or []
        identity = identity_from_filename(source.file_name)
        # Include failed/unparsed sources in ambiguity checks; they must not
        # disappear just because no course block survived their parse.
        by_name[identity.name].append((source, identity, rows))
    for name, group in by_name.items():
        if not name or len(group) != 2:
            continue
        if {entry[0].kind for entry in group} != {'中方', '英方'}:
            continue
        if any(not rows or not source.content_hash for source, _, rows in group):
            continue
        if group[0][0].content_hash == group[1][0].content_hash:
            continue
        if any({str(b.get('name') or '').strip() for b in rows} != {name}
               for _, _, rows in group):
            continue
        departments = {i.department for _, i, _ in group if i.department}
        roles = {i.role for _, i, _ in group if i.role}
        if len(departments) != 1 or len(roles) != 1:
            continue
        if not any(not i.confirmation_required for _, i, _ in group):
            continue
        target = MemberIdentity(name, next(iter(departments)), next(iter(roles)))
        # Do not overwrite explicit metadata or a prior correction.
        if any((b.get('department') and b['department'] != target.department)
               or (b.get('role') and b['role'] != target.role)
               or (b.get('member_key') and b['member_key'] not in {name, target.member_key})
               for _, _, rows in group for b in rows):
            continue
        for index, (source, identity, rows) in enumerate(group):
            if not identity.confirmation_required:
                continue
            evidence = dict(method='unique_compatible_complementary_pair',
                peer_source_hash=group[1-index][0].content_hash,
                source_hash=source.content_hash,
                original_department=identity.department, original_role=identity.role,
                department=target.department, role=target.role)
            for block in rows:
                block.update(department=target.department, role=target.role,
                             member_key=target.member_key)
                block['provenance'] = {**(block.get('provenance') or {}),
                                       'identity_completion': dict(evidence)}
