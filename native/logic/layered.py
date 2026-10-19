"""代替 dagre 的简化分层排版（从左到右），只给没保存位置的节点用。
步骤：去环 → 最长路径分层 → 几轮重心排序减少交叉 → 按层的最宽节点排列各列、列内自上而下排开、各列上下居中。
与 dagre 一样返回节点中心坐标；左上角对齐到 (0, 0)（dagre 的 marginx、marginy 为 0）。坐标不必与 dagre 相同。"""
from collections import deque


def layout(nodes, edges, nodesep=26, ranksep=48):
    """nodes：[(id, 宽, 高)]，按加入的顺序；edges：[(起点 id, 终点 id)]。返回 {id: (中心 x, 中心 y)}"""
    order = [n[0] for n in nodes]
    size = {n[0]: (n[1], n[2]) for n in nodes}
    if not order:
        return {}
    index = {v: i for i, v in enumerate(order)}

    # 去掉自环、重复边和连到不存在节点的边
    out = {v: [] for v in order}
    seen = set()
    for u, v in edges:
        if u == v or u not in index or v not in index or (u, v) in seen:
            continue
        seen.add((u, v))
        out[u].append(v)

    # 去环：按加入顺序深度优先，指回栈上节点的边反过来（与 dagre 的 dfs 去环一样）
    dag = set()
    state = dict.fromkeys(order, 0)  # 0 未访问，1 在栈上，2 已完成
    for root in order:
        if state[root]:
            continue
        state[root] = 1
        stack = [(root, iter(out[root]))]
        while stack:
            u, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[u] = 2
                stack.pop()
                continue
            if state[nxt] == 1:
                dag.add((nxt, u))
            else:
                dag.add((u, nxt))
                if state[nxt] == 0:
                    state[nxt] = 1
                    stack.append((nxt, iter(out[nxt])))
    succ = {v: [] for v in order}
    pred = {v: [] for v in order}
    for u, v in sorted(dag, key=lambda e: (index[e[0]], index[e[1]])):
        succ[u].append(v)
        pred[v].append(u)

    # 拓扑序（同时可以入队的按加入顺序），最长路径分层：每个节点在它所有前驱的右边一层
    indeg = {v: len(pred[v]) for v in order}
    queue = deque(v for v in order if indeg[v] == 0)
    topo = []
    while queue:
        u = queue.popleft()
        topo.append(u)
        for v in succ[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
    rank = {}
    for v in topo:
        rank[v] = max((rank[u] + 1 for u in pred[v]), default=0)
    # 没有前驱的节点往右靠到紧挨它的后继，免得被拉出长长的线
    for v in reversed(topo):
        if not pred[v] and succ[v]:
            rank[v] = min(rank[s] for s in succ[v]) - 1
    low = min(rank.values())
    for v in order:
        rank[v] -= low
    top = max(rank.values())

    layers = [[] for _ in range(top + 1)]
    for v in order:
        layers[rank[v]].append(v)

    def positions(ls):
        # 每个节点在本层里的相对位置（0～1），层的节点数不同也能比较
        return {v: (i + 0.5) / len(layer) for layer in ls for i, v in enumerate(layer)}

    def crossings(ls):
        pos = {v: i for layer in ls for i, v in enumerate(layer)}
        total = 0
        for r in range(len(ls) - 1):
            es = [(pos[u], pos[v]) for u in ls[r] for v in succ[u] if rank[v] == r + 1]
            for i in range(len(es)):
                a = es[i]
                for b in es[i + 1:]:
                    if (a[0] - b[0]) * (a[1] - b[1]) < 0:
                        total += 1
        return total

    best = [list(layer) for layer in layers]
    best_cross = crossings(best)
    for _ in range(4):
        for down in (True, False):
            rng = range(1, top + 1) if down else range(top - 1, -1, -1)
            for r in rng:
                pos = positions(layers)
                nb = pred if down else succ

                def bary(v):
                    ws = [pos[u] for u in nb[v]]
                    return sum(ws) / len(ws) if ws else pos[v]
                layers[r] = sorted(layers[r], key=bary)  # 稳定排序：重心相同时保持原来的先后
            c = crossings(layers)
            if c < best_cross:
                best, best_cross = [list(layer) for layer in layers], c
        if best_cross == 0:
            break

    # 坐标：每列宽度取该列最宽的节点，列间隔 ranksep；列内节点间隔 nodesep，各列上下居中
    result = {}
    x = 0
    for layer in best:
        if not layer:
            x += ranksep
            continue
        cw = max(size[v][0] for v in layer)
        total = sum(size[v][1] for v in layer) + nodesep * (len(layer) - 1)
        y = -total / 2
        for v in layer:
            h = size[v][1]
            result[v] = (x + cw / 2, y + h / 2)
            y += h + nodesep
        x += cw + ranksep
    min_top = min(cy - size[v][1] / 2 for v, (_, cy) in result.items())
    return {v: (cx, cy - min_top) for v, (cx, cy) in result.items()}
