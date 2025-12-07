import pygame
import random
import math
import pyaudio
import struct
import os
import sys
import subprocess
import threading

# Optional: psutil for CPU monitoring
try:
    import psutil
    PSUTIL_AVAILABLE = True
except Exception:
    PSUTIL_AVAILABLE = False

# ------------- GLOBALS THAT WILL BE UPDATED -------------
WIDTH, HEIGHT = 500, 500
CENTER_X, CENTER_Y = WIDTH // 2, HEIGHT // 2

SPHERE_RADIUS_BASE = 250   # base radius
SPHERE_RADIUS = SPHERE_RADIUS_BASE
FOV = 650

BG_COLOR = (5, 8, 20)
SPHERE_OUTLINE_COLOR = (10, 10, 20)

ROT_Y_SPEED = 0.4
ROT_X_SPEED = 0.18

NUM_DOTS = 2000          # number of dots
GOLD = (255, 215, 0)

# Audio config (still used to react the HUD)
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 44100

# --------- THEMES (for HUD inner colors) ---------
# Outer HUD stays cyan; only inner HUD colors change
THEMES = {
    1: {  # Blue + Orange/Gold (default)
        "name": "Orange / Gold",
        "quiet_core": (230, 120, 20),
        "loud_core": (255, 230, 80),
    },
    2: {  # Blue + Purple (cyber)
        "name": "Neon Purple",
        "quiet_core": (160, 80, 255),
        "loud_core": (255, 140, 255),
    },
    3: {  # White + Red (battle mode)
        "name": "Battle Red",
        "quiet_core": (230, 230, 230),
        "loud_core": (255, 80, 80),
    },
    4: {  # Green + Yellow (bio scanner)
        "name": "Bio Scanner",
        "quiet_core": (40, 200, 80),
        "loud_core": (220, 255, 140),
    },
}

current_theme = 1  # start with theme 1
ULTRA_BOLD = False  # toggle with 'U'

# --------- SPEAKING EFFECT (PULSES) ---------
VOICE_PULSES = []          # list of start times (ms)
VOICE_THRESHOLD = 0.15     # amplitude threshold to trigger a pulse
VOICE_PULSE_LIFE = 1200.0  # ms each pulse lives
last_amplitude = 0.0       # for edge detection

# --------- COMMAND LOG (display only specific commands) ---------
COMMANDS = [
    ("SIDD", "System boot complete."),
    ("YOU", "Initialize diagnostics."),
]
MAX_COMMANDS_SHOWN = 6


def recalc_layout(width, height):
    global WIDTH, HEIGHT, CENTER_X, CENTER_Y, SPHERE_RADIUS_BASE, SPHERE_RADIUS, FOV

    WIDTH, HEIGHT = width, height
    CENTER_X, CENTER_Y = WIDTH // 2, HEIGHT // 2

    # Sphere radius: use a percentage of the smallest dimension
    SPHERE_RADIUS_BASE = int(min(WIDTH, HEIGHT) * 0.32)
    SPHERE_RADIUS = SPHERE_RADIUS_BASE

    # FOV: scale with radius for consistent depth feeling
    FOV = SPHERE_RADIUS_BASE * 2.3


# -------------------- DOT ON SPHERE --------------------
class Dot:
    def __init__(self):
        # random point on sphere via spherical coordinates
        theta = random.uniform(0, 2 * math.pi)
        phi = random.uniform(0, math.pi)

        self.theta = theta
        self.phi = phi

        self.dtheta = random.uniform(-0.4, 0.4)
        self.dphi = random.uniform(-0.25, 0.25)

        self.x = 0
        self.y = 0
        self.z = 0

    def update(self, dt, rot_x, rot_y):
        # travel along the surface
        self.theta += self.dtheta * dt * 0.001
        self.phi += self.dphi * dt * 0.001

        # keep latitude in range
        if self.phi < 0:
            self.phi = -self.phi
            self.dphi *= -1
        elif self.phi > math.pi:
            self.phi = 2 * math.pi - self.phi
            self.dphi *= -1

        # spherical -> 3D cartesian (using current SPHERE_RADIUS)
        x = SPHERE_RADIUS * math.sin(self.phi) * math.cos(self.theta)
        y = SPHERE_RADIUS * math.cos(self.phi)
        z = SPHERE_RADIUS * math.sin(self.phi) * math.sin(self.theta)

        # rotate around Y axis
        cos_y = math.cos(rot_y)
        sin_y = math.sin(rot_y)
        xz = x * cos_y + z * sin_y
        zz = -x * sin_y + z * cos_y

        # rotate around X axis
        cos_x = math.cos(rot_x)
        sin_x = math.sin(rot_x)
        yz = y * cos_x - zz * sin_x
        zz2 = y * sin_x + zz * cos_x

        self.x, self.y, self.z = xz, yz, zz2

    def project(self):
        # camera along +z axis
        z_cam = self.z + SPHERE_RADIUS * 2.2
        if z_cam <= 1:
            z_cam = 1

        factor = FOV / z_cam

        sx = int(CENTER_X + self.x * factor)
        sy = int(CENTER_Y + self.y * factor)

        # depth factor [0..1], 1 = near front
        depth = max(0.0, min(1.0, 1 - (z_cam / (SPHERE_RADIUS_BASE * 3.0))))

        # radius scales with depth, min size ensures visibility
        radius = max(2, int(1 + depth * 3))

        r, g, b = GOLD
        brightness = 0.5 + depth * 0.7  # a bit brighter
        r = int(r * brightness)
        g = int(g * brightness)
        b = int(b * brightness)

        return sx, sy, radius, (r, g, b), depth


# -------------------- DRAW SHARP DOT --------------------
def draw_dot(surface, x, y, radius, color):
    pygame.draw.circle(surface, color, (x, y), radius)


# -------------------- UTILS --------------------
def lerp(a, b, t):
    return int(a + (b - a) * t)


def mix_color(c1, c2, t):
    """Blend two RGB colors with factor t in [0,1]."""
    return (
        lerp(c1[0], c2[0], t),
        lerp(c1[1], c2[1], t),
        lerp(c1[2], c2[2], t),
    )


# -------------------- ADVANCED SIDD HUD --------------------
def draw_sidd_hud(surface, t, amplitude):
    global ULTRA_BOLD, VOICE_PULSES

    center = (CENTER_X, CENTER_Y)
    ts = t * 0.001  # ms -> seconds

    # base size relative to screen
    base = int(min(WIDTH, HEIGHT) * 0.12)

    # smoother amplitude curve
    amp = min(max(amplitude, 0.0), 1.0)
    amp_visual = amp ** 0.7  # keeps it moving even with small sound

    # ---------- CONSTANT CYAN OUTER FRAME ----------
    CYAN = (0, 220, 255)
    CYAN_SOFT = (0, 170, 220)

    # thickness presets
    if ULTRA_BOLD:
        outer_ring_w = 5
        inner_ring_w = 3
        glow_ring_w = 2
        gap_arc_w = 3
        core_outline_w = 6
        flicker_ring_w = 3
        polygon_w = 3
        arc_ring_w = 4
        tick_w = 2
        scan_line_w = 2
        sweep_w = 6
        micro_dot_r = 3
        orbit_dot_r = 7
        pulse_w = 4
    else:
        outer_ring_w = 2
        inner_ring_w = 1
        glow_ring_w = 1
        gap_arc_w = 2
        core_outline_w = 3
        flicker_ring_w = 1
        polygon_w = 1
        arc_ring_w = 2
        tick_w = 1
        scan_line_w = 1
        sweep_w = 3
        micro_dot_r = 2
        orbit_dot_r = 5
        pulse_w = 2

    # ring radii
    r_inner_frame = int(base * 0.85)
    r_outer_frame = int(base * 1.4)
    r_outer_glow = int(base * 1.6)

    # outermost thin ring
    pygame.draw.circle(surface, CYAN_SOFT, center, r_outer_glow, glow_ring_w)
    # main outer ring
    pygame.draw.circle(surface, CYAN, center, r_outer_frame, outer_ring_w)
    # inner frame ring
    pygame.draw.circle(surface, CYAN, center, r_inner_frame, inner_ring_w)

    # spinning cyan "gaps" on the outer frame for subtle motion (color still cyan)
    gap_rect = pygame.Rect(0, 0, r_outer_frame * 2, r_outer_frame * 2)
    gap_rect.center = center
    gap_speed = 0.6
    for i in range(3):
        offset = ts * gap_speed + i * (2 * math.pi / 3)
        start_angle = offset
        end_angle = offset + math.pi / 7
        pygame.draw.arc(surface, CYAN_SOFT, gap_rect, start_angle, end_angle, gap_arc_w)

    # ---------- THEME-BASED INNER COLORS ----------
    theme = THEMES.get(current_theme, THEMES[1])
    quiet_core = theme["quiet_core"]
    loud_core = theme["loud_core"]
    inner_color = mix_color(quiet_core, loud_core, amp_visual)

    # ---------- PULSING CORE ----------
    core_radius = int(base * (0.45 + 0.25 * amp_visual))
    # outer core outline
    pygame.draw.circle(surface, inner_color, center, core_radius, core_outline_w)
    # inner flicker ring
    flicker_radius = int(core_radius * (0.5 + 0.2 * math.sin(ts * 4)))
    flicker_radius = max(4, flicker_radius)
    pygame.draw.circle(surface, inner_color, center, flicker_radius, flicker_ring_w)

    # ---------- ROTATING POLYGON "PROCESSOR" ----------
    sides = 6
    poly_radius = int(core_radius * 0.75)
    poly_angle_offset = ts * 1.2  # rotation speed
    poly_points = []
    for i in range(sides):
        ang = poly_angle_offset + (2 * math.pi * i / sides)
        x = CENTER_X + poly_radius * math.cos(ang)
        y = CENTER_Y + poly_radius * math.sin(ang)
        poly_points.append((x, y))
    pygame.draw.polygon(surface, inner_color, poly_points, polygon_w)

    # ---------- ARC RING (REACTIVE) ----------
    arc_radius = int(base * 1.05)
    arc_rect = pygame.Rect(0, 0, arc_radius * 2, arc_radius * 2)
    arc_rect.center = center
    num_arcs = 5
    for i in range(num_arcs):
        ang_off = ts * (0.9 + 0.2 * i)
        span = (math.pi / 7) + amp_visual * (math.pi / 10)
        start_ang = ang_off + i * (2 * math.pi / num_arcs)
        end_ang = start_ang + span
        pygame.draw.arc(surface, inner_color, arc_rect, start_ang, end_ang, arc_ring_w)

    # ---------- CYAN TICKS ON INNER FRAME ----------
    tick_count = 24
    tick_rot = ts * 0.5
    for i in range(tick_count):
        ang = tick_rot + (2 * math.pi * i / tick_count)
        r0 = r_inner_frame * 0.95
        r1 = r_inner_frame * 1.02
        x0 = CENTER_X + r0 * math.cos(ang)
        y0 = CENTER_Y + r0 * math.sin(ang)
        x1 = CENTER_X + r1 * math.cos(ang)
        y1 = CENTER_Y + r1 * math.sin(ang)
        pygame.draw.line(surface, CYAN_SOFT, (x0, y0), (x1, y1), tick_w)

    # ---------- RADIAL SCANNING LINES (REACTIVE) ----------
    num_lines = 18
    line_rot = ts * 1.8
    for i in range(num_lines):
        ang = line_rot + (2 * math.pi * i / num_lines)
        inner_r = core_radius * 1.05
        outer_r = r_inner_frame * (0.9 + 0.2 * amp_visual)
        x1 = CENTER_X + inner_r * math.cos(ang)
        y1 = CENTER_Y + inner_r * math.sin(ang)
        x2 = CENTER_X + outer_r * math.cos(ang)
        y2 = CENTER_Y + outer_r * math.sin(ang)
        pygame.draw.line(surface, inner_color, (x1, y1), (x2, y2), scan_line_w)

    # ---------- SWEEPING SCANNER BEAM ----------
    sweep_radius = r_outer_frame * 1.02
    sweep_rect = pygame.Rect(0, 0, sweep_radius * 2, sweep_radius * 2)
    sweep_rect.center = center
    sweep_angle = ts * 1.3
    sweep_span = math.pi / 20
    sweep_color = mix_color(inner_color, (255, 255, 255), 0.4)  # a bit brighter
    pygame.draw.arc(surface, sweep_color, sweep_rect, sweep_angle, sweep_angle + sweep_span, sweep_w)

    # ---------- ORBITING ENERGY DOT (REACTIVE) ----------
    orbit_r = r_inner_frame * 1.1
    orb_angle = ts * 2.2
    ox = CENTER_X + orbit_r * math.cos(orb_angle)
    oy = CENTER_Y + orbit_r * math.sin(orb_angle)

    orb_quiet = mix_color(inner_color, (255, 255, 255), 0.2)
    orb_loud = mix_color(inner_color, (255, 255, 255), 0.7)
    orb_color = mix_color(orb_quiet, orb_loud, amp_visual)
    pygame.draw.circle(surface, orb_color, (int(ox), int(oy)), orbit_dot_r)

    # ---------- INNER MICRO-DOTS (REACTIVE TEXTURE) ----------
    micro_count = 12
    for i in range(micro_count):
        ang = ts * 0.7 + i * (2 * math.pi / micro_count)
        r_m = core_radius * (0.3 + 0.5 * ((i % 3) / 2))
        x = CENTER_X + r_m * math.cos(ang)
        y = CENTER_Y + r_m * math.sin(ang)
        pygame.draw.circle(surface, inner_color, (int(x), int(y)), micro_dot_r)

    # ---------- SPEAKING PULSES (VOICE RINGS) ----------
    # expanding circles from core when voice pulses trigger
    alive_pulses = []
    for start_t in VOICE_PULSES:
        age = t - start_t  # ms
        if age < 0 or age > VOICE_PULSE_LIFE:
            continue
        alive_pulses.append(start_t)

        # 0..1 progress
        p = age / VOICE_PULSE_LIFE
        # radius from just outside core to near outer frame
        pulse_radius = core_radius * 1.2 + p * (r_outer_frame * 0.95 - core_radius * 1.2)
        # fade color from bright inner_color to cyan soft
        pulse_color = mix_color(inner_color, CYAN_SOFT, p)
        pygame.draw.circle(surface, pulse_color, center, int(pulse_radius), pulse_w)

    VOICE_PULSES = alive_pulses


# -------------------- AI BACKEND LISTENER --------------------
def listen_to_ai_backend(proc):
    global COMMANDS

    while True:
        try:
            line = proc.stdout.readline()
            if not line:
                break

            # TEMP: debug what we receive from AI.py
            print("AI STDOUT RAW:", repr(line))

            line = line.strip()
            if not line:
                continue

            # only keep explicit command lines
            if not line.startswith("[COMMAND]"):
                continue

            payload = line[len("[COMMAND]"):].strip()

            if payload.startswith("[YOU]"):
                msg = payload[len("[YOU]"):].strip()
                COMMANDS.append(("YOU", msg))

            elif payload.startswith("[SIDD]"):
                msg = payload[len("[SIDD]"):].strip()
                COMMANDS.append(("SIDD", msg))

            # keep history size under control
            if len(COMMANDS) > 100:
                COMMANDS = COMMANDS[-100:]

        except Exception as e:
            print("AI listener error:", e)
            break

# Wrap Text
def wrap_text_lines(font, text, max_width):
    words = text.split()
    if not words:
        return [""]

    lines = []
    current = words[0]

    for word in words[1:]:
        test = current + " " + word
        if font.size(test)[0] <= max_width:
            current = test
        else:
            lines.append(current)
            current = word

    lines.append(current)
    return lines

# -------------------- CONVERSATION PANEL --------------------
def draw_conversation_panel(surface, x, y, w, h):
    """Conversation panel with multi-line word wrapping."""
    panel_bg = (7, 10, 25)
    panel_border = (40, 60, 120)
    text_primary = (220, 230, 255)
    text_dim = (150, 170, 210)

    font_title = pygame.font.SysFont("consolas", 16)
    font_small = pygame.font.SysFont("consolas", 14)
    font_tiny = pygame.font.SysFont("consolas", 12)

    panel_rect = pygame.Rect(x, y, w, h)
    pygame.draw.rect(surface, panel_bg, panel_rect, border_radius=10)
    pygame.draw.rect(surface, panel_border, panel_rect, 1, border_radius=10)

    pad = 10
    content_x = x + pad
    content_y = y + pad
    content_w = w - pad * 2

    # ---------- HEADER ----------
    surface.blit(
        font_title.render("CONVERSATION", True, text_primary),
        (content_x, content_y),
    )
    surface.blit(
        font_tiny.render("YOU  ⇄  SIDD", True, text_dim),
        (content_x, content_y + 20),
    )

    # divider
    divider_y = content_y + 36
    pygame.draw.line(
        surface, (60, 80, 140),
        (content_x, divider_y),
        (x + w - pad, divider_y),
        1,
    )

    # ---------- CHAT AREA ----------
    line_y = divider_y + 8
    line_h = 18

    speaker_col_w = 70
    text_x = content_x + speaker_col_w
    text_max_w = content_w - speaker_col_w

    bottom_limit = y + h - pad - 20  # keep space for footer

    # draw newest messages at bottom of list (already handled by COMMANDS[-N:])
    msgs = COMMANDS[:]  # we will stop when no vertical space
    # ensure we don't try to draw more than can fit reasonably
    # (rough estimate; overflow protection is via bottom_limit)
    max_est_lines = max(2, (bottom_limit - line_y) // line_h)

    # we want last messages first, so slice, then reverse
    msgs_to_draw = msgs[-10:]  # last 10 exchanges is usually enough
    # but we actually draw in chronological order
    # so that they appear top -> bottom
    for speaker, text in msgs_to_draw:
        if line_y > bottom_limit:
            break

        s_color = (0, 220, 255) if speaker.upper() == "SIDD" else (200, 210, 255)

        # wrap message into multiple lines
        wrapped_lines = wrap_text_lines(font_small, text, text_max_w)

        for i, line_text in enumerate(wrapped_lines):
            if line_y > bottom_limit:
                break

            # first line: show speaker label; next lines: just indent
            if i == 0:
                speaker_surface = font_small.render(f"{speaker}:", True, s_color)
                surface.blit(speaker_surface, (content_x, line_y))
            # message text
            text_surface = font_small.render(line_text, True, text_primary)
            surface.blit(text_surface, (text_x, line_y))

            line_y += line_h

        if line_y > bottom_limit:
            break

    # ---------- FOOTER ----------
    footer_text = "Press K to add demo lines"
    footer_surface = font_tiny.render(footer_text, True, text_dim)
    surface.blit(
        footer_surface,
        (content_x, y + h - pad - 12),
    )

def draw_system_performance(surface, x, y, w):
    font_title = pygame.font.SysFont("consolas", 16)
    font_small = pygame.font.SysFont("consolas", 14)
    font_tiny = pygame.font.SysFont("consolas", 12)

    panel_bg = (10, 15, 35)
    panel_border = (40, 60, 120)
    text_color = (210, 225, 255)
    text_dim = (150, 170, 210)

    h = 140
    rect = pygame.Rect(x, y, w, h)

    pygame.draw.rect(surface, panel_bg, rect, border_radius=10)
    pygame.draw.rect(surface, panel_border, rect, 1, border_radius=10)

    inner_pad = 10
    content_x = x + inner_pad
    content_y = y + inner_pad

    # --- Header ---
    title = font_title.render("SYSTEM PERFORMANCE", True, text_color)
    surface.blit(title, (content_x, content_y))

    # small "LIVE" badge on the right
    live_text = font_tiny.render("LIVE", True, (0, 220, 255))
    live_box = live_text.get_rect()
    live_box.top = content_y + 2
    live_box.right = x + w - inner_pad
    pygame.draw.rect(surface, (5, 25, 40), live_box.inflate(10, 4), border_radius=6)
    surface.blit(live_text, (live_box.left + 5, live_box.top + 2))

    # divider
    divider_y = content_y + 22
    pygame.draw.line(
        surface,
        (60, 80, 140),
        (content_x, divider_y),
        (x + w - inner_pad, divider_y),
        1,
    )

    if not PSUTIL_AVAILABLE:
        msg = font_small.render("psutil not available", True, text_color)
        surface.blit(msg, (content_x, divider_y + 8))
        return

    cpu = psutil.cpu_percent()
    mem = psutil.virtual_memory().percent
    disk = psutil.disk_usage('/').percent
    net = psutil.net_io_counters()
    net_mb = (net.bytes_sent + net.bytes_recv) / (1024 * 1024)

    labels = ["CPU", "MEMORY", "DISK", "NETWORK"]
    values = [cpu, mem, disk, min(net_mb, 100.0)]
    units = ["%", "%", "%", "MB"]

    row_y = divider_y + 10
    row_gap = 22

    label_col_x = content_x
    value_col_x = x + w - inner_pad - 40  # right-aligned numeric text
    bar_x = content_x + 70
    bar_w = w - (bar_x - x) - inner_pad

    for i in range(4):
        # label
        label_surf = font_small.render(f"{labels[i]} :", True, text_dim)
        surface.blit(label_surf, (label_col_x, row_y))

        # numeric value (right-aligned)
        val_str = f"{values[i]:5.1f}{units[i]}"
        val_surf = font_small.render(val_str, True, text_color)
        val_rect = val_surf.get_rect()
        val_rect.right = x + w - inner_pad
        val_rect.top = row_y
        surface.blit(val_surf, val_rect.topleft)

        # bar under the label/value row
        bar_y = row_y + 14
        pct = max(0.0, min(values[i] / 100.0, 1.0))

        pygame.draw.rect(
            surface,
            (20, 30, 50),
            (bar_x, bar_y, bar_w, 8),
            border_radius=4,
        )

        if pct > 0:
            color = (80, 200, 120) if pct < 0.7 else (220, 80, 80)
            pygame.draw.rect(
                surface,
                color,
                (bar_x, bar_y, int(bar_w * pct), 8),
                border_radius=4,
            )

        row_y += row_gap

# -------------------- ANALYTICS PANELS OUTSIDE SPHERE --------------------
def draw_analytics(surface, t, amplitude, fps):
    global current_theme, ULTRA_BOLD

    # --- Colors ---
    panel_bg = (10, 15, 35)
    panel_border = (40, 60, 120)
    text_color = (200, 220, 255)

    theme = THEMES.get(current_theme, THEMES[1])
    theme_name = theme["name"]
    amp_pct = int(amplitude * 100)

    # smoother amp for visuals
    amp_visual = min(max(amplitude, 0.0), 1.0) ** 0.8

    # --- FONT ---
    font_small = pygame.font.SysFont("consolas", 16)
    font_tiny = pygame.font.SysFont("consolas", 13)

    # ---------- TOP-LEFT: ANALYTICS PANEL ----------
    info_w, info_h = 230, 110
    info_x, info_y = 20, 20
    info_rect = pygame.Rect(info_x, info_y, info_w, info_h)

    pygame.draw.rect(surface, panel_bg, info_rect, border_radius=8)
    pygame.draw.rect(surface, panel_border, info_rect, 1, border_radius=8)

    lines = [
        "SIDD AI — ANALYTICS",
        f"Theme: {theme_name}",
        f"Ultra-Bold: {'ON' if ULTRA_BOLD else 'OFF'}",
        f"Amplitude: {amp_pct:3d} %",
        f"FPS: {int(fps):3d}",
    ]
    for i, text in enumerate(lines):
        surf = font_small.render(text, True, text_color)
        surface.blit(surf, (info_x + 10, info_y + 8 + i * 18))

    # ---------- CONVERSATION UNDER ANALYTICS ----------
    conv_x = info_x
    conv_y = info_y + info_h + 12
    conv_w = info_w
    # height until roughly mid-screen or just above sphere bottom
    conv_h = HEIGHT - (info_y + info_h + 40)   # 40 = bottom margin
    draw_conversation_panel(surface, conv_x, conv_y, conv_w, conv_h)

    # ---------- BOTTOM-CENTER AUDIO LEVEL BAR ----------
    bar_w, bar_h = 320, 16
    bar_x = CENTER_X - bar_w // 2
    bar_y = HEIGHT - bar_h - 30

    outer_bar = pygame.Rect(bar_x, bar_y, bar_w, bar_h)
    pygame.draw.rect(surface, panel_bg, outer_bar, border_radius=8)
    pygame.draw.rect(surface, panel_border, outer_bar, 1, border_radius=8)

    # fill based on amplitude
    fill_w = int(bar_w * amp_visual)
    if fill_w > 0:
        # green → yellow → red based on amplitude
        low = (80, 200, 120)
        high = (255, 80, 80)
        fill_color = mix_color(low, high, amp_visual)
        inner_bar = pygame.Rect(
            bar_x + 2,
            bar_y + 2,
            fill_w - 4 if fill_w > 4 else 0,
            bar_h - 4,
        )
        if inner_bar.width > 0:
            pygame.draw.rect(surface, fill_color, inner_bar, border_radius=6)

    # label
    label = font_tiny.render("VOICE LEVEL", True, text_color)
    surface.blit(label, (bar_x, bar_y - 16))

    # ---------- BOTTOM-RIGHT: REAL-TIME SIGNAL ----------
    graph_w, graph_h = 220, 120
    graph_x = WIDTH - graph_w - 20
    graph_y = HEIGHT - graph_h - 30

    graph_rect = pygame.Rect(graph_x, graph_y, graph_w, graph_h)
    pygame.draw.rect(surface, panel_bg, graph_rect, border_radius=8)
    pygame.draw.rect(surface, panel_border, graph_rect, 1, border_radius=8)

    g_label = font_tiny.render("REAL-TIME SIGNAL", True, text_color)
    surface.blit(g_label, (graph_x + 8, graph_y + 6))

    # ---------- SYSTEM PERFORMANCE ABOVE REAL-TIME SIGNAL ----------
    # same width, directly above graph
    sys_w = graph_w
    sys_h = 140  # same as in draw_system_performance
    sys_x = graph_x
    sys_y = graph_y - sys_h - 10  # 10px gap above graph
    draw_system_performance(surface, sys_x, sys_y, sys_w)

    # bars inside REAL-TIME SIGNAL panel
    num_bars = 12
    gap = 4
    bar_width = (graph_w - (num_bars + 1) * gap) // num_bars
    time_factor = t * 0.004

    quiet_color = theme["quiet_core"]
    loud_color = theme["loud_core"]

    for i in range(num_bars):
        phase = time_factor + i * 0.6
        base_wave = (math.sin(phase) + 1) / 2  # 0..1
        # scale by amplitude
        value = (0.25 + 0.75 * amp_visual) * base_wave
        h = int((graph_h - 40) * value)
        bx = graph_x + gap + i * (bar_width + gap)
        by = graph_y + graph_h - 10 - h

        color = mix_color(quiet_color, loud_color, value)
        pygame.draw.rect(surface, color, (bx, by, bar_width, h), border_radius=4)


# -------------------- MAIN LOOP --------------------
def main():
    pygame.init()

    global SPHERE_RADIUS, current_theme, ULTRA_BOLD, last_amplitude, VOICE_PULSES, COMMANDS

    # ---- START SIDD AI BACKEND (AI.py) ----
    ai_process = None
    try:
        # AI.py is assumed to be in the same folder as frontend.py
        script_dir = os.path.dirname(os.path.abspath(__file__))
        ai_script = os.path.join(script_dir, "AI.py")

        # start AI with stdout piped so we can read its messages
        ai_process = subprocess.Popen(
            [sys.executable, ai_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1  # line-buffered
        )
        # start listener thread for conversation log
        threading.Thread(
            target=listen_to_ai_backend,
            args=(ai_process,),
            daemon=True
        ).start()

        print("AI backend started:", ai_script)
    except Exception as e:
        print("Could not start AI backend:", e)

    # get current display resolution and start in a resizable window
    info = pygame.display.Info()
    start_w, start_h = info.current_w // 1, info.current_h // 1

    recalc_layout(start_w, start_h)
    screen = pygame.display.set_mode((start_w, start_h), pygame.RESIZABLE)
    pygame.display.set_caption("Audio Reactive Golden Sphere + SIDD HUD + Analytics")

    clock = pygame.time.Clock()

    dots = [Dot() for _ in range(NUM_DOTS)]

    # ---- Audio setup ----
    pa = pyaudio.PyAudio()
    stream = pa.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=RATE,
        input=True,
        frames_per_buffer=CHUNK
    )

    rot_x = 0.0
    rot_y = 0.0
    t = 0.0  # time for animation (ms)

    running = True
    try:
        while running:
            dt = clock.tick(60)
            t += dt  # time in ms

            if ai_process is not None and ai_process.poll() is not None:
                print("Backend stopped")
                running = False
                break

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False

                # handle window resize
                if event.type == pygame.VIDEORESIZE:
                    new_w, new_h = event.w, event.h
                    recalc_layout(new_w, new_h)
                    screen = pygame.display.set_mode((new_w, new_h), pygame.RESIZABLE)

                # -------- THEME SWITCH KEYS (1–4) + ULTRA BOLD (U) --------
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_1:
                        current_theme = 1
                    elif event.key == pygame.K_2:
                        current_theme = 2
                    elif event.key == pygame.K_3:
                        current_theme = 3
                    elif event.key == pygame.K_4:
                        current_theme = 4
                    elif event.key == pygame.K_u:
                        ULTRA_BOLD = not ULTRA_BOLD
                        
            # ---- Read audio chunk & compute amplitude ----
            def _open_stream():
                """Try to (re)open the input stream with the same settings."""
                return pa.open(
                    format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    input=True,
                    frames_per_buffer=CHUNK
                )

            # attempt to read, recover on error, and fallback to silent buffer
            try:
                data = stream.read(CHUNK, exception_on_overflow=False)
            except Exception as e:
                # log the error, attempt a graceful reopen, then fallback to silence
                print("Audio read error:", repr(e))

                # try to close & reopen once
                try:
                    try:
                        stream.stop_stream()
                    except Exception:
                        pass
                    try:
                        stream.close()
                    except Exception:
                        pass
                except Exception:
                    pass

                try:
                    stream = _open_stream()
                    print("Reopened audio stream.")
                    data = stream.read(CHUNK, exception_on_overflow=False)
                except Exception as e2:
                    print("Reopen failed:", repr(e2))
                    # fallback to a silent buffer (paInt16 -> 2 bytes per sample)
                    data = b'\x00' * (CHUNK * 2)

            # make sure data has expected length before unpacking
            expected_bytes = CHUNK * 2  # paInt16 -> 2 bytes per frame
            if len(data) < expected_bytes:
                # pad if short
                data = data.ljust(expected_bytes, b'\x00')
            elif len(data) > expected_bytes:
                # trim if too long
                data = data[:expected_bytes]

            # unpack safely (will produce CHUNK ints)
            try:
                samples = struct.unpack(f'{CHUNK}h', data)
            except struct.error as e:
                # worst-case: if unpack fails, treat as silence
                print("struct.unpack error:", repr(e))
                samples = (0,) * CHUNK

            # RMS (root mean square) for volume — robust against all-zero samples
            sum_squares = 0.0
            for s in samples:
                sum_squares += s * s
            rms = math.sqrt(sum_squares / CHUNK) if CHUNK > 0 else 0.0

            # Normalize RMS to [0,1] (tune 3000 for sensitivity)
            amplitude = min(rms / 3000.0, 1.0)

            # RMS (root mean square) for volume
            sum_squares = 0.0
            for s in samples:
                sum_squares += s * s
            rms = math.sqrt(sum_squares / CHUNK)

            # Normalize RMS to [0,1] (tune 3000 for sensitivity)
            amplitude = min(rms / 3000.0, 1.0)

            # ----- SPEAKING PULSE TRIGGER (on rising edge over threshold) -----
            if amplitude > VOICE_THRESHOLD and last_amplitude <= VOICE_THRESHOLD:
                VOICE_PULSES.append(t)
            last_amplitude = amplitude

            # sphere radius fixed
            SPHERE_RADIUS = SPHERE_RADIUS_BASE

            # global sphere rotation
            rot_y += ROT_Y_SPEED * dt * 0.001
            rot_x += ROT_X_SPEED * dt * 0.001

            # update dot positions
            for d in dots:
                d.update(dt, rot_x, rot_y)

            # draw farthest first
            dots_sorted = sorted(dots, key=lambda d: d.z)

            # ---- DRAW ----
            screen.fill(BG_COLOR)

            # sphere outline
            pygame.draw.circle(
                screen,
                SPHERE_OUTLINE_COLOR,
                (CENTER_X, CENTER_Y),
                int(SPHERE_RADIUS * 0.9),
                1
            )

            # sphere dots
            for d in dots_sorted:
                sx, sy, radius, color, depth = d.project()
                if 0 <= sx < WIDTH and 0 <= sy < HEIGHT:
                    draw_dot(screen, sx, sy, radius, color)

            # SIDD HUD always on top, inside sphere
            draw_sidd_hud(screen, t, amplitude)

            # Working analytics around the sphere (includes commands panel & CPU)
            fps = clock.get_fps()
            draw_analytics(screen, t, amplitude, fps)

            pygame.display.flip()
    finally:
        # clean up audio
        stream.stop_stream()
        stream.close()
        pa.terminate()
        pygame.quit()

        # ---- STOP SIDD AI BACKEND ----
        if ai_process is not None and ai_process.poll() is None:
            try:
                ai_process.terminate()
                ai_process.wait(timeout=5)
                print("AI backend terminated.")
            except Exception as e:
                print("Error terminating AI backend:", e)


if __name__ == "__main__":
    main()
