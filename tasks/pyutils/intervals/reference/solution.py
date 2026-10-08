def merge(intervals):
    items = sorted(tuple(i) for i in intervals)
    out = []
    for s, e in items:
        if s > e:
            raise ValueError((s, e))
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def insert(intervals, new):
    return merge(list(intervals) + [tuple(new)])


def subtract(intervals, cut):
    cs, ce = cut
    out = []
    for s, e in merge(intervals):
        if e < cs or s > ce:
            out.append((s, e))
            continue
        if s < cs:
            out.append((s, cs - 1))
        if e > ce:
            out.append((ce + 1, e))
    return out


def total_length(intervals):
    return sum(e - s + 1 for s, e in merge(intervals))
