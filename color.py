from math import atan2, cos, exp, pi, sin, sqrt

def rgb2xyz(rgb):
    """Converts RGB color into XYZ format."""

    def format(c):
        # c = c / 255.
        if c > 0.04045: c = ((c + 0.055) / 1.055) ** 2.4
        else: c = c / 12.92
        return c * 100
    rgb = list(map(format, rgb))
    xyz = [None, None, None]
    xyz[0] = rgb[0] * 0.4124 + rgb[1] * 0.3576 + rgb[2] * 0.1805
    xyz[1] = rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722
    xyz[2] = rgb[0] * 0.0193 + rgb[1] * 0.1192 + rgb[2] * 0.9505
    return xyz

def xyz2lab(xyz):
    """Converts XYZ pixel array to LAB format."""
    # Implementation derived from http://www.easyrgb.com/en/math.php

    def format(c):
        if c > 0.008856: c = c ** (1. / 3.)
        else: c = (7.787 * c) + (16. / 116.)
        return c
    xyz[0] = xyz[0] / 95.047
    xyz[1] = xyz[1] / 100.00
    xyz[2] = xyz[2] / 108.883
    xyz = list(map(format, xyz))
    lab = [None, None, None]
    lab[0] = (116. * xyz[1]) - 16.
    lab[1] = 500. * (xyz[0] - xyz[1])
    lab[2] = 200. * (xyz[1] - xyz[2])
    return lab

def rgb2lab(rgb):
    """Converts RGB color into LAB format."""

    return xyz2lab(rgb2xyz(rgb))

def ciede2000(Lab_1, Lab_2):
    """Calculates CIEDE2000 color distance between two CIE L*a*b* colors."""
    # From https://github.com/lovro-i/CIEDE2000

    C_25_7 = 6103515625 # 25**7

    L1, a1, b1 = Lab_1[0], Lab_1[1], Lab_1[2]
    L2, a2, b2 = Lab_2[0], Lab_2[1], Lab_2[2]
    C1 = sqrt(a1**2 + b1**2)
    C2 = sqrt(a2**2 + b2**2)
    C_ave = (C1 + C2) / 2
    G = 0.5 * (1 - sqrt(C_ave**7 / (C_ave**7 + C_25_7)))

    L1_, L2_ = L1, L2
    a1_, a2_ = (1 + G) * a1, (1 + G) * a2
    b1_, b2_ = b1, b2

    C1_ = sqrt(a1_**2 + b1_**2)
    C2_ = sqrt(a2_**2 + b2_**2)

    if b1_ == 0 and a1_ == 0: h1_ = 0
    elif a1_ >= 0: h1_ = atan2(b1_, a1_)
    else: h1_ = atan2(b1_, a1_) + 2 * pi

    if b2_ == 0 and a2_ == 0: h2_ = 0
    elif a2_ >= 0: h2_ = atan2(b2_, a2_)
    else: h2_ = atan2(b2_, a2_) + 2 * pi

    dL_ = L2_ - L1_
    dC_ = C2_ - C1_
    dh_ = h2_ - h1_
    if C1_ * C2_ == 0: dh_ = 0
    elif dh_ > pi: dh_ -= 2 * pi
    elif dh_ < -pi: dh_ += 2 * pi
    dH_ = 2 * sqrt(C1_ * C2_) * sin(dh_ / 2)

    L_ave = (L1_ + L2_) / 2
    C_ave = (C1_ + C2_) / 2

    _dh = abs(h1_ - h2_)
    _sh = h1_ + h2_
    C1C2 = C1_ * C2_

    if _dh <= pi and C1C2 != 0: h_ave = (h1_ + h2_) / 2
    elif _dh  > pi and _sh < 2 * pi and C1C2 != 0: h_ave = (h1_ + h2_) / 2 + pi
    elif _dh  > pi and _sh >= 2 * pi and C1C2 != 0: h_ave = (h1_ + h2_) / 2 - pi
    else: h_ave = h1_ + h2_

    T = (1 - 0.17 * cos(h_ave - pi / 6)
        + 0.24 * cos(2 * h_ave)
        + 0.32 * cos(3 * h_ave + pi / 30)
        - 0.2 * cos(4 * h_ave - 63 * pi / 180))

    h_ave_deg = h_ave * 180 / pi
    if h_ave_deg < 0: h_ave_deg += 360
    elif h_ave_deg > 360: h_ave_deg -= 360
    dTheta = 30 * exp(-(((h_ave_deg - 275) / 25)**2))

    R_C = 2 * sqrt(C_ave**7 / (C_ave**7 + C_25_7))
    S_C = 1 + 0.045 * C_ave
    S_H = 1 + 0.015 * C_ave * T

    Lm50s = (L_ave - 50)**2
    S_L = 1 + 0.015 * Lm50s / sqrt(20 + Lm50s)
    R_T = -sin(dTheta * pi / 90) * R_C

    k_L, k_C, k_H = 1, 1, 1

    f_L = dL_ / k_L / S_L
    f_C = dC_ / k_C / S_C
    f_H = dH_ / k_H / S_H

    dE_00 = sqrt(f_L**2 + f_C**2 + f_H**2 + R_T * f_C * f_H)
    return dE_00
