"""Rigid pose interpolation and self-contained frame playback artifact generation."""

from __future__ import annotations

import math


def quaternion(matrix):
    """Unit w,x,y,z quaternion from a validated row-major rotation matrix."""
    m = matrix
    trace = sum(m[i][i] for i in range(3))
    if trace > 0:
        s = math.sqrt(trace + 1) * 2
        q = [s / 4, (m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s]
    else:
        i = max(range(3), key=lambda k: m[k][k])
        j, k = (i + 1) % 3, (i + 2) % 3
        s = math.sqrt(1 + m[i][i] - m[j][j] - m[k][k]) * 2
        q = [(m[k][j] - m[j][k]) / s, 0.0, 0.0, 0.0]
        q[i + 1] = s / 4
        q[j + 1] = (m[j][i] + m[i][j]) / s
        q[k + 1] = (m[k][i] + m[i][k]) / s
    length = math.sqrt(sum(v * v for v in q))
    return [v / length for v in q]


def interpolate_rotation(first, second, fraction):
    a, b = quaternion(first), quaternion(second)
    cosine = sum(x * y for x, y in zip(a, b, strict=True))
    if cosine < 0:
        b = [-v for v in b]
        cosine = -cosine
    if cosine > 0.9995:
        q = [x + fraction * (y - x) for x, y in zip(a, b, strict=True)]
    else:
        angle = math.acos(max(-1, min(1, cosine)))
        q = [
            (math.sin((1 - fraction) * angle) * x + math.sin(fraction * angle) * y)
            / math.sin(angle)
            for x, y in zip(a, b, strict=True)
        ]
    length = math.sqrt(sum(v * v for v in q))
    w, x, y, z = [v / length for v in q]
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]
