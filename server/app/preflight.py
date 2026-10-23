from sqlmodel import select
from .models import Device, MediaAsset
from .compiler import required_assets, compile_preview
from .loader import load_layout


def preflight(program, session):
    layout=load_layout(session)
    # Validate explicit tracks before interrupting an already playing program.
    preview=compile_preview(program,layout,max_ms=60_000)
    ids=required_assets(program,layout)
    assets=session.exec(select(MediaAsset).where(MediaAsset.id.in_(ids))).all() if ids else []
    total=sum({a.sha256:a.size for a in assets}.values())
    high=any(a.technical.get("delivery",{}).get("fps",0)>30.1 for a in assets)
    devices=[]; errors=[]; warnings=[]
    rolling = any(item.get("type") == "scheduler" for item in program.get("items", []))
    mismatches = sorted({(slot["surface_id"], slot["asset_id"]) for slot in preview.get("slots", [])
                         if slot["surface_id"] in layout.surfaces and slot["asset_id"] in layout.assets
                         and layout.assets[slot["asset_id"]].orientation
                         and layout.assets[slot["asset_id"]].orientation != layout.surfaces[slot["surface_id"]].orientation})
    for sid, aid in mismatches:
        warnings.append(f"{layout.surfaces[sid].id}: no prepared {layout.surfaces[sid].orientation} alternative for {aid}; using the selected anchor")
    for d in session.exec(select(Device).where(Device.role=="player")).all():
        if not d.surface_id: continue
        device_ids = required_assets(program, layout, {d.surface_id})
        device_assets = [a for a in assets if a.id in device_ids]
        device_total = sum({a.sha256:a.size for a in device_assets}.values())
        budget=d.capabilities.get("cache_budget_mb")
        problem=bool(budget and budget>0 and device_total>budget*1024*1024)
        if problem and rolling:
            warnings.append(f"{d.name}: full roster is {device_total/1024**2:.0f} MB; playback uses a rolling scheduled working set within the {budget} MB cache")
        elif problem: errors.append(f"{d.name}: {device_total/1024**2:.0f} MB required exceeds {budget} MB cache budget")
        if high and not d.capabilities.get("h264_720p60"): warnings.append(f"{d.name}: 60 FPS decoding unverified or unsupported; prepare and select a 30 FPS compatibility clip if needed")
        if high and d.capabilities.get("display_refresh_hz",60)<59: warnings.append(f"{d.name}: display refresh rate is below 60 Hz")
        devices.append({"id":d.id,"name":d.name,"cache_budget_mb":budget,"required_bytes":device_total,"assets":len(device_assets),"fits":not problem if budget else None,"capabilities":d.capabilities})
    if not devices: warnings.append("No player surfaces are connected/configured; physical playback remains unverified")
    return {"required_bytes":total,"assets":len(assets),"devices":devices,"errors":errors,"warnings":warnings,"playback_verified":False}
