"""Coordinate-invariant surface differential geometry for sampled continuity checks."""

from __future__ import annotations

import math

from nx_mcp.runtime import NXToolError


def dot(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def shape_operator(du, dv, duu, duv, dvv):
    normal = [
        du[1] * dv[2] - du[2] * dv[1],
        du[2] * dv[0] - du[0] * dv[2],
        du[0] * dv[1] - du[1] * dv[0],
    ]
    area = math.sqrt(dot(normal, normal))
    if area <= 1e-14:
        raise NXToolError("NX_SINGULAR_SURFACE", "Surface parameterization is singular at a sample")
    normal = [v / area for v in normal]
    e, f, g = dot(du, du), dot(du, dv), dot(dv, dv)
    determinant = e * g - f * f
    if determinant <= 1e-24 * max(1.0, e * g):
        raise NXToolError("NX_SINGULAR_SURFACE", "Surface metric is singular at a sample")
    inverse = [[g / determinant, -f / determinant], [-f / determinant, e / determinant]]
    second = [[dot(normal, duu), dot(normal, duv)], [dot(normal, duv), dot(normal, dvv)]]
    tangent = [[du[i], dv[i]] for i in range(3)]
    dual = [
        [sum(tangent[i][k] * inverse[k][j] for k in range(2)) for j in range(2)] for i in range(3)
    ]
    shape = [
        [
            sum(
                dual[i][k] * second[k][column] * dual[j][column]
                for k in range(2)
                for column in range(2)
            )
            for j in range(3)
        ]
        for i in range(3)
    ]
    return normal, shape


def continuity_difference(first, second):
    normal_a, shape_a = first
    normal_b, shape_b = second
    cosine = dot(normal_a, normal_b)
    orientation = 1 if cosine >= 0 else -1
    angle = math.degrees(math.acos(min(1.0, abs(cosine))))
    curvature = math.sqrt(
        sum((shape_a[i][j] - orientation * shape_b[i][j]) ** 2 for i in range(3) for j in range(3))
    )
    return angle, curvature
