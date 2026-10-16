from binflatten.export import to_svg, to_dxf, to_preview_svg, _unit_scale, _dxf_line


def cutGroup(geometry, params, scale, width):
    paths = []
    for cut in geometry.cut_segments:
        a, b = cut['points'] * scale
        paths.append(f'<path d="M {a[0]:.5f},{a[1]:.5f} L {b[0]:.5f},{b[1]:.5f}"/>')
    return f'<g id="release-cut" stroke="{params.cut_color}" fill="none" stroke-width="{width:.5f}">'+'\n'.join(paths)+'</g>'


def toSimpleSvg(geometry, params):
    _, scale = _unit_scale(params)
    return to_svg(geometry, params).replace('</svg>', cutGroup(geometry, params, scale, .1*scale)+'\n</svg>')


def toSimpleDxf(geometry, params):
    _, scale = _unit_scale(params)
    codes = []
    for cut in geometry.cut_segments:
        a, b = cut['points'] * scale
        codes.extend(_dxf_line(a, b, 'CUT'))
    header, marker, entities = to_dxf(geometry, params).partition('0\nSECTION\n2\nENTITIES\n')
    if not marker or '0\nENDSEC\n' not in entities:
        raise ValueError('DXF has no ENTITIES section')
    return header+marker+entities.replace('0\nENDSEC\n', '\n'.join(codes)+'\n0\nENDSEC\n', 1)


def toSimplePreview(pattern, geometry, params):
    return to_preview_svg(pattern, geometry, params).replace('</svg>', cutGroup(geometry, params, 1, .6)+'\n</svg>')
