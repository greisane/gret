from gpu_extras.batch import batch_for_shader
# import bgl
import blf
import bpy
import gpu

shader_image = gpu.shader.from_builtin('2D_IMAGE')
shader_solid = gpu.shader.from_builtin('2D_UNIFORM_COLOR')
h1_font_size = 19
p_font_size = 15
line_height_h1 = int(1.3 * h1_font_size)
line_height_p = int(1.3 * p_font_size)
font_id = 0
rect_texcoords = (0, 0), (1, 0), (1, 1), (0, 1)

class UVSheetTheme:
    unselectable = (0.8, 0.8, 0.8, 0.7) # (0.6, 0.6, 0.6, 0.6)
    unselected = (0.8, 0.8, 0.8, 0.7)
    hovered = (1.0, 1.0, 1.0, 0.8)
    selected = (0.4, 1.0, 0.2, 1.0)
    bad = (1.0, 0.2, 0.2, 1.0)
    grid = (0.8, 0.8, 0.8, 0.2)
    marquee = (1.0, 1.0, 1.0, 1.0)
    border = (0.42, 0.42, 0.42, 1.0)

    @property
    def background(self):
        return bpy.context.preferences.themes[0].image_editor.space.back

    @property
    def selected(self):
        return bpy.context.preferences.themes[0].image_editor.edge_select

def draw_image(x0, y0, x1, y1, image, nearest=False):
    if not image:
        return
    # XXX Filters not exposed in gpu module, workaround with bgl is simple enough however the state
    # is messed up afterwards, causing other things to break.
    nearest = False
    if nearest:
        image.gl_load()
        shader_image.bind()
        bgl.glEnable(bgl.GL_BLEND)
        bgl.glEnable(bgl.GL_TEXTURE_2D)
        bgl.glBindTexture(bgl.GL_TEXTURE_2D, image.bindcode)
        bgl.glTexParameteri(bgl.GL_TEXTURE_2D, bgl.GL_TEXTURE_MIN_FILTER, bgl.GL_NEAREST)
        bgl.glTexParameteri(bgl.GL_TEXTURE_2D, bgl.GL_TEXTURE_MAG_FILTER, bgl.GL_NEAREST)
    else:
        texture = gpu.texture.from_image(image)
        shader_image.bind()
        shader_image.uniform_sampler("image", texture)
    batch_for_shader(shader_image, 'TRI_FAN', {
        "pos": ((x0, y0), (x1, y0), (x1, y1), (x0, y1)),
        "texCoord": rect_texcoords,
    }).draw(shader_image)
    if nearest:
        bgl.glDisable(bgl.GL_TEXTURE_2D)
        image.gl_free()

def draw_point(x, y, color, size=1.0):
    if len(color) == 3:
        color = *color, 1.0
    use_blend = color[3] < 1.0
    if use_blend:
        gpu.state.blend_set('ALPHA')
    gpu.state.point_size_set(size)
    shader_solid.bind()
    shader_solid.uniform_float("color", color)
    batch_for_shader(shader_solid, 'POINTS', {
        "pos": [(x, y)],
    }).draw(shader_solid)
    if use_blend:
        gpu.state.blend_set('NONE')

def draw_box_fill(x0, y0, x1, y1, color):
    if len(color) == 3:
        color = *color, 1.0
    use_blend = color[3] < 1.0
    if use_blend:
        gpu.state.blend_set('ALPHA')
    shader_solid.bind()
    shader_solid.uniform_float("color", color)
    batch_for_shader(shader_solid, 'TRI_FAN', {
        "pos": ((x0, y0), (x1, y0), (x1, y1), (x0, y1)),
    }).draw(shader_solid)
    if use_blend:
        gpu.state.blend_set('NONE')

def draw_box(x0, y0, x1, y1, color, width=1.0):
    if len(color) == 3:
        color = *color, 1.0
    use_blend = color[3] < 1.0
    if use_blend:
        gpu.state.blend_set('ALPHA')
    gpu.state.line_width_set(width)
    shader_solid.bind()
    shader_solid.uniform_float("color", color)
    batch_for_shader(shader_solid, 'LINE_LOOP', {
        "pos": ((x0, y0), (x1, y0), (x1, y1), (x0, y1)),
    }).draw(shader_solid)
    if use_blend:
        gpu.state.blend_set('NONE')

def draw_grid(x, y, grid_width, grid_height, num_cols, num_rows, color, width=1.0):
    if len(color) == 3:
        color = *color, 1.0
    use_blend = color[3] < 1.0
    if use_blend:
        gpu.state.blend_set('ALPHA')
    gpu.state.line_width_set(width)
    shader_solid.bind()
    shader_solid.uniform_float("color", color)
    x0, y0 = x, y
    x1, y1 = x + grid_width * num_cols, y + grid_height * num_rows
    lines = []
    for n in range(num_cols + 1):
        x = x0 + n * grid_width
        lines.extend(((x, y0), (x, y1)))
    for n in range(num_rows + 1):
        y = y0 + n * grid_height
        lines.extend(((x0, y), (x1, y)))
    batch_for_shader(shader_solid, 'LINES', {
        "pos": lines,
    }).draw(shader_solid)
    if use_blend:
        gpu.state.blend_set('NONE')

def draw_help_box(texts, title="", padding=16.0, left_margin=30.0, bottom_margin=30.0, width_override=None):
    if not texts and not title:
        return

    # Guess dimensions
    if width_override is not None:
        box_width = width_override
    else:
        max_string_len = len(max(texts, key=lambda p: len(p[0]))[0])
        box_width = max_string_len * (0.5 * p_font_size)
    box_width += padding * 2.0

    box_height = len(texts) * line_height_p
    if title:
        if texts:
            box_height += 8.0
        box_height += line_height_h1
    box_height += padding * 1.75

    # Draw background
    draw_box_fill(left_margin, bottom_margin, left_margin + box_width, bottom_margin + box_height,
        color=(0.0, 0.0, 0.0, 0.3))

    # Draw text starting from the bottom
    y = bottom_margin + padding + line_height_p * 0.25
    blf.color(font_id, 1.0, 1.0, 1.0, 1.0)

    for text in reversed(texts):
        blf.size(font_id, p_font_size, 60)
        blf.position(font_id, left_margin + padding, y, 0)
        blf.draw(font_id, text)
        y += line_height_p

    if title:
        if texts:
            y += 8.0
        blf.size(font_id, h1_font_size, 60)
        blf.position(font_id, left_margin + padding, y, 0)
        blf.draw(font_id, title)
