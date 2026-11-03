#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mfg_names.py — 游戏目录名 → 中文显示名"""

import re

# 键为归一化后的目录名（小写、去掉空格/标点）
NAMES = {
    "3dmark": "3DMark",
    "428shibuyascrambleen": "428 被封锁的涩谷",
    "baldursgate3": "博德之门 3",
    "batmanarkhamcitygoty": "蝙蝠侠：阿卡姆之城 GOTY",
    "battlebrothers": "战场兄弟",
    "battlefield1": "战地 1",
    "battlefieldv": "战地 V",
    "bodycam": "Bodycam",
    "broforce": "武装兄弟连",
    "cardsurvivaltropicalisland": "卡牌生存：热带岛屿",
    "conscript": "应征入伍",
    "crimsondesert": "红色沙漠",
    "crosshairx": "Crosshair X",
    "crusaderkingsiii": "十字军之王 3",
    "cuberacer": "Cube Racer",
    "darksouls2scholarofthefirstsin": "黑暗之魂 2：原罪学者",
    "darksoulspreparetodieedition": "黑暗之魂：受死版",
    "darkestdungeon": "暗黑地牢",
    "deadcells": "死亡细胞",
    "deadlock": "死锁",
    "diablo2resurrected": "暗黑破坏神 2：重制版",
    "dishonored": "耻辱",
    "dworigins": "真·三国无双：起源",
    "eldenring": "艾尔登法环",
    "fc26": "EA SPORTS FC 26",
    "hardwest": "西部硬汉",
    "heartsofironiv": "钢铁雄心 4",
    "heroesofmightandmagicoldenera": "魔法门之英雄无敌：上古纪元",
    "huntshowdown1896": "猎杀：对决 1896",
    "kingdomrushfrontiers": "王国保卫战：前线",
    "kingdomrushgenesis": "王国保卫战：创世纪",
    "kingdomcomedeliverance2": "天国拯救 2",
    "losslessscaling": "无损缩放",
    "marvelrivals": "漫威争锋",
    "mechabellum": "机甲对决",
    "metro2033redux": "地铁 2033 重制版",
    "metrolastlightredux": "地铁：最后的曙光 重制版",
    "mewgenics": "喵基因",
    "minionmasters": "随从大师",
    "mk10": "真人快打 X",
    "monstertrain2": "怪物火车 2",
    "mountbladeiibannerlord": "骑马与砍杀 2：霸主",
    "ninjagaiden4": "忍者龙剑传 4",
    "nioh3": "仁王 3",
    "noita": "女巫",
    "nordicashes": "北欧灰烬",
    "obsstudio": "OBS Studio",
    "papersplease": "请出示证件",
    "pbz": "影之刃零",
    "portal2": "传送门 2",
    "romanceofthethreekingdoms14": "三国志 14",
    "saltandsanctuary": "盐与避难所",
    "spacewar": "Spacewar（Steam 测试）",
    "stateofdecayyose": "腐烂国度：元年版",
    "streetfighter6": "街头霸王 6",
    "stalker2heartofchornobyl": "潜行者 2：切尔诺贝利之心",
    "thebindingofisaac": "以撒的结合",
    "thebindingofisaacrebirth": "以撒的结合：重生",
    "theevilwithin": "恶灵附身",
    "tomclancysrainbowsixsiege": "彩虹六号：围攻",
    "totalwarthreekingdoms": "全面战争：三国",
    "totalwarwarhammerii": "全面战争：战锤 2",
    "totalwarwarhammeriii": "全面战争：战锤 3",
    "underlords": "刀塔霸业",
    "vtubestudio": "VTube Studio",
    "wallpaperengine": "壁纸引擎",
    "warmsnow": "暖雪",
    "witchfire": "女巫之火",
    "wuchangfallenofthefeathers": "明末：渊虚之羽",
    "xcomenemyunknown": "幽浮：未知敌人",
}

_PUNCT = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


def normalize(name):
    s = name.lower()
    s = s.replace("&", "and")
    s = _PUNCT.sub("", s)
    return s


def zh_name(dirname):
    """目录名 → 中文名；找不到则原样返回"""
    key = normalize(dirname)
    if not key:
        return dirname
    hit = NAMES.get(key)
    if hit:
        return hit
    # 宽松匹配：目录名包含映射键 / 映射键包含目录名
    if len(key) >= 5:
        for k, v in NAMES.items():
            if len(k) < 5:
                continue
            if key.startswith(k) or k.startswith(key) or key in k or k in key:
                return v
    return dirname
