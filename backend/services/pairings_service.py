from itertools import combinations
from random import shuffle


def pod_sizes(n_players: int, num_pods: int) -> list[int]:
    k = max(1, int(num_pods))
    k = min(k, n_players)
    base = n_players // k
    rest = n_players % k
    sizes = [base + (1 if i < rest else 0) for i in range(k)]
    sizes.sort(reverse=True)
    return sizes


def pairs_in_group(group: list[int]) -> list[tuple[int, int]]:
    g = sorted(group)
    return [(g[i], g[j]) for i in range(len(g)) for j in range(i + 1, len(g))]


def counts_key(counts: list[list[int]]) -> tuple:
    n = len(counts)
    out = []
    for i in range(n):
        for j in range(i + 1, n):
            out.append(counts[i][j])
    return tuple(out)


def counts_from_key(key: tuple, n: int) -> list[list[int]]:
    counts = [[0] * n for _ in range(n)]
    idx = 0
    for i in range(n):
        for j in range(i + 1, n):
            v = int(key[idx])
            idx += 1
            counts[i][j] = v
            counts[j][i] = v
    return counts


def missing_pairs(counts: list[list[int]]) -> int:
    n = len(counts)
    m = 0
    for i in range(n):
        for j in range(i + 1, n):
            if counts[i][j] == 0:
                m += 1
    return m


def max_count(counts: list[list[int]]) -> int:
    n = len(counts)
    mx = 0
    for i in range(n):
        for j in range(i + 1, n):
            if counts[i][j] > mx:
                mx = counts[i][j]
    return mx


def sum_sq(counts: list[list[int]]) -> int:
    n = len(counts)
    s = 0
    for i in range(n):
        for j in range(i + 1, n):
            v = counts[i][j]
            s += v * v
    return s


def apply_partition(counts: list[list[int]], pods: list[list[int]]) -> list[list[int]]:
    newc = [row[:] for row in counts]
    for pod in pods:
        for (i, j) in pairs_in_group(pod):
            newc[i][j] += 1
            newc[j][i] += 1
    return newc


def gen_partitions(indices: list[int], sizes: list[int]) -> list[list[list[int]]]:
    sizes = list(sizes)
    sizes.sort(reverse=True)

    res = []
    indices = sorted(indices)

    def rec(remaining: list[int], si: int, acc: list[list[int]]):
        if si >= len(sizes):
            if not remaining:
                res.append([g[:] for g in acc])
            return
        size = sizes[si]
        if len(remaining) < size:
            return

        first = remaining[0]
        for comb in combinations(remaining[1:], size - 1):
            group = [first] + list(comb)
            group_set = set(group)
            new_remaining = [x for x in remaining if x not in group_set]
            acc.append(sorted(group))
            rec(new_remaining, si + 1, acc)
            acc.pop()

    rec(indices, 0, [])
    return res


def first_round_with_hosts(players: list[str], num_pods: int, hosts: list[str]) -> list[list[str]]:
    n = len(players)
    sizes = pod_sizes(n, num_pods)
    k = len(sizes)

    host_set: list[str] = []
    seen: set[str] = set()
    for h in hosts or []:
        h = (h or "").strip()
        if not h or h in seen:
            continue
        if h not in players:
            continue
        seen.add(h)
        host_set.append(h)

    host_set = host_set[:k]

    remaining = [p for p in players if p not in set(host_set)]
    shuffle(remaining)

    pods: list[list[str]] = [[] for _ in range(k)]

    host_sorted = sorted(host_set, key=lambda x: x.lower())
    for i, h in enumerate(host_sorted):
        if i >= k:
            break
        pods[i].append(h)

    idx = 0
    for i in range(k):
        want = sizes[i] - len(pods[i])
        if want <= 0:
            continue
        pods[i].extend(remaining[idx:idx + want])
        idx += want

    flat = [p for pod in pods for p in pod]
    if sorted(flat) != sorted(players):
        shuffle(remaining)
        pods = []
        idx = 0
        for s in sizes:
            pods.append(remaining[idx:idx + s])
            idx += s

    return pods


def build_rounds(
    players: list[str],
    num_pods: int,
    max_rounds: int,
    fixed_first_round: list[list[str]] | None = None,
) -> list[list[list[str]]]:
    n = len(players)
    sizes = pod_sizes(n, num_pods)
    partitions = gen_partitions(list(range(n)), sizes)

    start_counts = [[0] * n for _ in range(n)]
    rounds_idx: list[list[list[int]]] = []

    if fixed_first_round:
        name_to_idx = {name: i for i, name in enumerate(players)}
        fixed_idx: list[list[int]] = []
        used: set[int] = set()
        for pod in fixed_first_round:
            pod_idx: list[int] = []
            for name in pod:
                if name not in name_to_idx:
                    continue
                pod_idx.append(name_to_idx[name])
            fixed_idx.append(sorted(pod_idx))
            used.update(pod_idx)

        ok_sizes = sorted([len(p) for p in fixed_idx], reverse=True) == sorted(sizes, reverse=True)
        ok_used = len(used) == n
        if ok_sizes and ok_used:
            rounds_idx.append(fixed_idx)
            start_counts = apply_partition(start_counts, fixed_idx)

    start_key = counts_key(start_counts)

    from collections import deque

    best_depth_solution = None
    visited = set([(start_key, len(rounds_idx))])

    q = deque()
    q.append((start_key, len(rounds_idx), rounds_idx[:]))

    while q:
        key, depth, path = q.popleft()
        counts = counts_from_key(key, n)

        miss = missing_pairs(counts)
        if miss == 0:
            best_depth_solution = (depth, path, key)
            break

        if depth >= max_rounds:
            continue

        best_candidates = []
        for pods in partitions:
            newc = apply_partition(counts, pods)
            cost = (missing_pairs(newc), max_count(newc), sum_sq(newc))
            newkey = counts_key(newc)
            st = (newkey, depth + 1)
            if st in visited:
                continue
            visited.add(st)
            best_candidates.append((cost, pods, newkey))

        best_candidates.sort(key=lambda x: x[0])

        for (cost, pods, newkey) in best_candidates[:60]:
            q.append((newkey, depth + 1, path + [pods]))

    if best_depth_solution is None:
        counts = start_counts
        while len(rounds_idx) < max_rounds:
            best = None
            for pods in partitions:
                newc = apply_partition(counts, pods)
                cost = (missing_pairs(newc), max_count(newc), sum_sq(newc))
                if best is None or cost < best[0]:
                    best = (cost, pods, newc)
            rounds_idx.append(best[1])
            counts = best[2]
    else:
        depth, rounds_idx, key = best_depth_solution
        counts = counts_from_key(key, n)

        while len(rounds_idx) < max_rounds:
            best = None
            for pods in partitions:
                newc = apply_partition(counts, pods)
                cost = (max_count(newc), sum_sq(newc))
                if best is None or cost < best[0]:
                    best = (cost, pods, newc)
            rounds_idx.append(best[1])
            counts = best[2]

    rounds_named = []
    for pods in rounds_idx:
        round_pods = []
        for pod in pods:
            round_pods.append([players[i] for i in pod])
        rounds_named.append(round_pods)
    return rounds_named


def apply_round_to_raffle(raffle_list: list[dict], state: dict, round_no: int) -> None:
    rounds = state.get("rounds") or []
    phase = state.get("phase") or "ready"
    if round_no < 1 or round_no > len(rounds):
        return

    pods = rounds[round_no - 1]
    assign = {}
    for t, group in enumerate(pods, start=1):
        for p in group:
            assign[p] = (t, group)

    for e in raffle_list:
        if e.get("deck_id") is None:
            continue
        owner = (e.get("deckOwner") or "").strip()
        if not owner:
            continue
        if owner in assign:
            t, group = assign[owner]
            e["pairing_round"] = round_no
            e["pairing_table"] = t
            e["pairing_players"] = group
        else:
            e["pairing_round"] = round_no
            e["pairing_table"] = None
            e["pairing_players"] = []
        e["pairing_phase"] = phase
