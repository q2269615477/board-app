# -*- coding: utf-8 -*-
import re
from pathlib import Path

root = Path(__file__).resolve().parent.parent
kc = (root / "static/js/klinecharts.min.js").read_text(encoding="utf-8", errors="ignore")
pro = (root / "static/js/klinecharts-pro.umd.js").read_text(encoding="utf-8", errors="ignore")

names = set(re.findall(r"[A-Za-z]*[Oo]verlay[A-Za-z]*", kc))
print("overlay names:", sorted(names)[:100])

acts = set(re.findall(r'"(on[A-Z][a-zA-Z]+)"', kc))
print("on* actions:", sorted(a for a in acts if "verlay" in a or "Draw" in a or "draw" in a))

# execute API surface often like: getOverlayById:function
api = set(re.findall(r"([a-zA-Z]{4,40}):function\(", kc))
api2 = set(re.findall(r"([a-zA-Z]{4,40}):function\(", pro[:200000]))
interesting = sorted(
    x
    for x in api
    if any(k in x.lower() for k in ("overlay", "draw", "subscribe", "data", "convert"))
)
print("kc methods:", interesting[:60])

# pro public methods on class
print("pro _chartApi count", pro.count("_chartApi"))
print("pro _chart count", pro.count("_chart"))
for pat in ["getOverlayInfos", "getOverlays", "getOverlayInfo", "overlays"]:
    print(pat, "kc", kc.count(pat), "pro", pro.count(pat))

# snippet around createOverlay in kc
m = re.search(r".{0,30}createOverlay.{0,100}", kc)
print("createOverlay snip:", m.group()[:130] if m else None)
m = re.search(r".{0,30}getOverlayById.{0,100}", kc)
print("getOverlayById snip:", m.group()[:130] if m else None)

# look for overlay store getInstance or getOverlays by different name
for pat in [r"getInstance", r"getOverlay", r"OverlayStore", r"overlayStore", r"getFigures"]:
    print(pat, kc.count(pat.replace("\\", "")))
