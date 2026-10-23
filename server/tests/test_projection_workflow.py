from contextlib import contextmanager
import json
import random
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from app.models import GenerationProject, StoryboardShot, GenerationTake, MediaAsset, Approval, SourceFile, LibraryJob
from app.api import generation
from app.comfy import projects
from app.media import library, ffmpeg
from app.loader import asset_url
from app.compiler import AssetInfo, SurfaceInfo, Layout, compile_preview, required_assets, slot_to_events
from app.geometry import Rect


@pytest.fixture
def database():
    engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s: yield s
    engine.dispose()


@pytest.fixture
def api(database):
    app=FastAPI();app.include_router(generation.router)
    app.dependency_overrides[generation.get_session]=lambda:database
    with TestClient(app) as c: yield c


def seed(database):
    p=GenerationProject(name="Creature",concept="A ghost")
    database.add(p);database.commit();database.refresh(p)
    sh=StoryboardShot(project_id=p.id,name="Appear",prompt="Rises into view",sound="Breath")
    database.add(sh);database.commit();database.refresh(sh)
    return p,sh


def ready_character(database,p,api):
    t=projects.create_take(database,p,None,"character")
    t.status="done";database.add(t);database.commit()
    assert api.post(f"/api/generation/takes/{t.id}/approve").status_code==200
    database.refresh(p)
    return t


def test_animation_requires_current_image_and_character(database,api):
    p,sh=seed(database)
    url=f"/api/generation/projects/{p.id}/render"
    assert api.post(url,json={"stage":"video","shot_id":sh.id}).status_code==409
    ready_character(database,p,api)
    assert api.post(url,json={"stage":"video","shot_id":sh.id}).status_code==409
    image=projects.create_take(database,p,sh,"image");image.status="done";database.add(image);database.commit()
    assert api.post(f"/api/generation/takes/{image.id}/approve").status_code==200
    assert api.post(url,json={"stage":"video","shot_id":sh.id}).status_code==200
    assert api.put(f"/api/generation/shots/{sh.id}",json={"name":"Changed", "prompt":"Different action"}).status_code==200
    assert api.post(url,json={"stage":"video","shot_id":sh.id}).status_code==409
    assert api.post(f"/api/generation/takes/{image.id}/approve").status_code==409
    assert database.get(GenerationTake,image.id) is not None


def test_changing_selected_character_invalidates_existing_images(database,api):
    p,sh=seed(database);ready_character(database,p,api)
    image=projects.create_take(database,p,sh,"image");image.status="done";database.add(image);database.commit()
    ready_character(database,p,api)
    assert api.post(f"/api/generation/takes/{image.id}/approve").status_code==409


def test_publish_requires_video_review_and_is_idempotent(database,api):
    p,sh=seed(database);ready_character(database,p,api)
    image=projects.create_take(database,p,sh,"image");image.status="done";database.add(image);database.commit()
    api.post(f"/api/generation/takes/{image.id}/approve");database.refresh(sh)
    t=projects.create_take(database,p,sh,"video")
    a=MediaAsset(title="Private take",published=False,status="ready")
    database.add(a);database.commit();database.refresh(a)
    t.asset_id=a.id;t.status="done";database.add(t);database.commit()
    assert api.post(f"/api/generation/takes/{t.id}/publish").status_code==409
    assert api.post(f"/api/generation/takes/{t.id}/approve").status_code==200
    assert api.post(f"/api/generation/takes/{t.id}/approve").status_code==200
    assert len(database.exec(select(Approval).where(Approval.take_id==t.id)).all())==1
    published=api.post(f"/api/generation/takes/{t.id}/publish")
    assert published.status_code==200 and published.json()["published"] is True
    assert api.post(f"/api/generation/takes/{t.id}/publish").status_code==200


def test_foreign_project_shot_rejected(database,api):
    p,sh=seed(database);other=GenerationProject(name="Other");database.add(other);database.commit()
    assert api.post(f"/api/generation/projects/{other.id}/render",json={"stage":"image","shot_id":sh.id}).status_code==400


def test_motion_revision_reuses_approved_image_but_requires_new_video_review(database,api):
    p,sh=seed(database);ready_character(database,p,api)
    image=projects.create_take(database,p,sh,"image");image.status="done";database.add(image);database.commit()
    api.post(f"/api/generation/takes/{image.id}/approve");database.refresh(sh)
    url=f"/api/generation/projects/{p.id}/render"
    response=api.post(url,json={"stage":"video","shot_id":sh.id,"motion_prompt":"Raise one hand; keep face illuminated"})
    assert response.status_code==200
    take=database.get(GenerationTake,response.json()["id"])
    assert take.provenance["motion_prompt"]=="Raise one hand; keep face illuminated"
    assert take.provenance["image_take_id"]==image.id
    assert projects.approved(database,image) and not projects.approved(database,take)
    assert api.post(url,json={"stage":"image","shot_id":sh.id,"motion_prompt":"Invalid override"}).status_code==400
    assert api.post(url,json={"stage":"video","shot_id":sh.id,"motion_prompt":123}).status_code==400


@pytest.mark.parametrize("direction,phase",[("right","exit"),("right","enter"),("left","exit"),("left","enter")])
def test_finished_character_crosses_stationary_window(tmp_path,direction,phase):
    import subprocess
    src=tmp_path/"source.mp4";dst=tmp_path/"travel.mp4"
    ffmpeg._run([ffmpeg.FFMPEG,"-y","-v","error","-f","lavfi","-i","color=black:s=96x144:r=60:d=2","-f","lavfi","-i","sine=frequency=440:duration=2","-vf","drawbox=x=36:y=40:w=24:h=64:color=white:t=fill","-c:v","libx264","-c:a","aac","-shortest",str(src)])
    ffmpeg.transcode(ffmpeg.build_video_cmd(src,dst,{"fps":60,"travel_direction":direction,"travel_phase":phase,"travel_seconds":1},ffmpeg.probe(src)))
    raw=subprocess.check_output([ffmpeg.FFMPEG,"-v","error","-i",str(dst),"-f","rawvideo","-pix_fmt","gray","-"])
    frames=[raw[i:i+96*144] for i in range(0,len(raw),96*144)]
    def center(frame):
        points=[i%96 for i,v in enumerate(frame) if v>100]
        return sum(points)/len(points) if points else None
    centers=[center(f) for f in frames]
    visible=[c for c in centers if c is not None]
    assert len(frames)==120 and ffmpeg.probe(dst)["has_audio"]
    if phase=="exit": assert centers[0] is not None and centers[-1] is None
    else: assert centers[0] is None and centers[-1] is not None
    sign=1 if direction=="right" else -1
    assert sign*(visible[-1]-visible[0])>35
    assert all(sign*(b-a)>=-1 for a,b in zip(visible,visible[1:]))


def test_take_sets_freeze_distinct_assets_in_explicit_order(database,api,monkeypatch):
    from app import loader
    p,a=seed(database);ready_character(database,p,api)
    b=StoryboardShot(project_id=p.id,name="B",surface="B",position=1)
    database.add(b);database.commit();database.refresh(b)
    assets={}
    for sh in (a,b):
        image=projects.create_take(database,p,sh,"image");image.status="done";database.add(image);database.commit()
        api.post(f"/api/generation/takes/{image.id}/approve");database.refresh(sh)
    def video(sh,name,set_name="",index=0):
        t=projects.create_take(database,p,sh,"video",set_name=set_name,sequence_index=index,travel_direction="right" if set_name else "",travel_phase="exit" if set_name else "")
        asset=MediaAsset(title=name,status="ready",published=True,duration_ms=5000)
        database.add(asset);database.commit();database.refresh(asset)
        t.asset_id=asset.id;t.status="done";database.add(t);database.commit()
        api.post(f"/api/generation/takes/{t.id}/approve")
        assets[asset.id]=AssetInfo(asset.id,5000)
        return asset.id
    original=[video(a,"originalA"),video(b,"originalB")]
    travel=[video(b,"travelB","Travel",0),video(a,"travelA","Travel",1)]
    layout=Layout({s:SurfaceInfo(s,Rect(i*60,0,48,72)) for i,s in enumerate("AB")},{s:[s+"device"] for s in "AB"},assets)
    monkeypatch.setattr(loader,"load_layout",lambda session:layout)
    url=f"/api/generation/projects/{p.id}/encounter"
    base={"surfaces":{"A":"A","B":"B"}}
    response=api.post(url,json=base);assert response.status_code==200
    assert [t["asset_id"] for t in response.json()["items"][0]["tracks"]]==original
    response=api.post(url,json={**base,"set_name":"Travel"});assert response.status_code==200
    tracks=response.json()["items"][0]["tracks"]
    assert [t["asset_id"] for t in tracks]==travel
    assert tracks[1]["at"]==5 and tracks[0]["transition"]=={"type":"cut","duration":0}
    assert api.post(url,json={**base,"set_name":"Missing"}).status_code==400


def test_encounter_absolute_timing_audio_and_required_assets():
    layout=Layout({s:SurfaceInfo(s,Rect(i*60,0,48,72)) for i,s in enumerate("ABC")},{s:[s+"device"] for s in "ABC"},{"ghost":AssetInfo("ghost",5000)})
    item={"type":"encounter","id":"monster","tracks":[{"surface_id":s,"asset_id":"ghost","at":i*6,"audio":s!="B"} for i,s in enumerate("ABC")]}
    prog={"items":[{"type":"block","repeat":2,"items":[item]}],"loop_mode":"once"}
    compiled=compile_preview(prog,layout)
    assert [s["start"] for s in compiled["slots"]]==[0,6000,12000,17000,23000,29000]
    assert compiled["slots"][1]["audio"] is False
    assert required_assets(prog,layout)=={"ghost"}
    item["tracks"].append({"surface_id":"A","asset_id":"ghost","at":2})
    with pytest.raises(ValueError,match="overlap"):compile_preview({"items":[item],"loop_mode":"once"},layout)


@pytest.mark.parametrize("path,targets,appearance,rotation",[
    ("Pack/Ghosts/Window - Hollusion/Haunt_V.mp4",["window","hollusion"],"black",90),
    ("Pack/Ghosts/Wall + Doorway - Background/Haunt_H.mp4",["wall","doorway"],"enclosure",0),
    ("Pack/Ghosts/Window - Shadow/Haunt_H.mp4",["window"],"shadow",0),
])
def test_classification_facets(path,targets,appearance,rotation):
    c=library.classify(path)
    assert c["targets"]==targets;assert c["appearance"]==appearance;assert c["content_rotation"]==rotation


def test_bundled_projector_files_group_horizontal_and_vertical_variants():
    horizontal=library.classify("Shitthatcamewiththeprojector/Horizontal Individual Decorations/02 - Ghostly Apparitions 2 - Slender Specter Bent Out of Shape - H.mp4")
    vertical=library.classify("Shitthatcamewiththeprojector/Vertical Individual Decorations/02 - Ghostly Apparitions 2 - Slender Specter Bent Out of Shape - V.mp4")
    assert horizontal["collection"] == vertical["collection"] == "Ghostly Apparitions 2"
    assert horizontal["scene_key"] == vertical["scene_key"]
    assert horizontal["title"] == "Slender Specter Bent Out of Shape"
    assert horizontal["character"] == vertical["character"] == "Slender Specter"
    assert horizontal["action"] == "Bent Out of Shape"
    assert horizontal["targets"] == ["window", "wall", "tv"]
    assert horizontal["orientation"] == "horizontal" and horizontal["content_rotation"] == 0
    assert vertical["orientation"] == "vertical" and vertical["content_rotation"] == 90
    assert vertical["appearance"] == "enclosure" and vertical["review"] is False


def test_native_catalogue_asset_uses_read_only_source_url():
    asset=MediaAsset(title="Native",source_file_id="source123",preset={"profile":"native"},rendition_path="")
    assert asset_url(asset)=="/files/source/source123"
    asset.rendition_path="renditions/rotated.mp4"
    assert asset_url(asset)=="/files/media/renditions/rotated.mp4"


def test_scan_idempotence_overrides_and_missing(database,tmp_path,monkeypatch):
    @contextmanager
    def scope(): yield database
    monkeypatch.setattr(library,"session_scope",scope)
    monkeypatch.setattr(library,"SOURCE_ROOT",tmp_path)
    calls=[]
    def probe(path):
        calls.append(path.name)
        if path.name=="broken.mp4":raise ValueError("invalid video")
        return {"has_video":True,"duration":5,"fps":29.97}
    monkeypatch.setattr(ffmpeg,"probe",probe)
    for name in ("ghost_Win_H.mp4","._ghost.mp4","broken.mp4"):(tmp_path/name).write_bytes(b"test")
    job=LibraryJob(kind="scan");database.add(job);database.commit()
    library.scan(job.id)
    rows=database.exec(select(SourceFile)).all();assert len(rows)==3
    ghost=next(r for r in rows if "Win" in r.relative_path)
    ghost.overrides={"character":"Custom ghost","content_rotation":270};database.add(ghost);database.commit()
    library.scan(job.id)
    assert len(database.exec(select(SourceFile)).all())==3
    database.refresh(ghost)
    assert library.effective(ghost)["character"]=="Custom ghost"
    assert calls.count("ghost_Win_H.mp4")==1
    assert calls.count("._ghost.mp4")==0
    # A missing file is flagged, never removed from the inventory.
    ghost.relative_path="gone.mp4";database.add(ghost);database.commit()
    library.scan(job.id);database.refresh(ghost);assert ghost.status=="missing"


def test_ffmpeg_cadence_rotation_audio(tmp_path):
    src=tmp_path/"test.mp4";dst=tmp_path/"processed.mp4"
    ffmpeg._run([ffmpeg.FFMPEG,"-y","-v","error","-f","lavfi","-i","testsrc2=size=128x72:rate=24000/1001:duration=1","-f","lavfi","-i","sine=frequency=440:duration=1","-c:v","libx264","-c:a","aac","-shortest",str(src)])
    info=ffmpeg.probe(src)
    ffmpeg.transcode(ffmpeg.build_video_cmd(src,dst,{"fps":0,"content_rotation":270,"fade_in":0.1,"fade_out":0.1},info))
    out=ffmpeg.probe(dst)
    assert (out["width"],out["height"])==(72,128)
    assert abs(out["fps"]-info["fps"])<0.01
    assert out["has_audio"] and abs(out["duration"]-info["duration"])<0.1


def test_new_character_cannot_animate_old_approved_start(database, api):
    p, sh = seed(database); ready_character(database, p, api)
    image = projects.create_take(database, p, sh, "image")
    image.status = "done"; database.add(image); database.commit()
    api.post(f"/api/generation/takes/{image.id}/approve"); database.refresh(sh)
    ready_character(database, p, api)
    result = api.post(f"/api/generation/projects/{p.id}/render", json={"stage":"video", "shot_id":sh.id})
    assert result.status_code == 409
    assert "Character selection changed" in result.json()["detail"]


def test_encounter_simultaneous_audio_defaults_to_one_source():
    layout=Layout({s:SurfaceInfo(s,Rect(i*60,0,48,72)) for i,s in enumerate("ABC")},{s:[s+"device"] for s in "ABC"},{"ghost":AssetInfo("ghost",5000)})
    item={"type":"encounter","tracks":[{"surface_id":s,"asset_id":"ghost","at":0} for s in "ABC"]}
    slots=compile_preview({"items":[item],"loop_mode":"once"},layout)["slots"]
    assert [s["audio"] for s in slots] == [True,False,False]
    item["tracks"][2]["audio"]=True
    assert compile_preview({"items":[item],"loop_mode":"once"},layout)["slots"][2]["audio"] is True


def test_database_migration_backup_and_repeat(tmp_path, monkeypatch):
    import sqlite3
    from app import db, config
    path=tmp_path/"legacy.db"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE mediaasset (id VARCHAR PRIMARY KEY, title VARCHAR)")
        c.execute("INSERT INTO mediaasset VALUES ('old-id','Keep me')")
    engine=create_engine(f"sqlite:///{path}")
    monkeypatch.setattr(db,"DB_PATH",path); monkeypatch.setattr(db,"engine",engine)
    monkeypatch.setattr(config,"WORK_DIR",tmp_path)
    db.init_db(); db.init_db()
    backups=list((tmp_path/"backups").glob("*.db"))
    assert len(backups)==1
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT id,title,published FROM mediaasset").fetchone()==('old-id','Keep me',1)
        assert c.execute("PRAGMA user_version").fetchone()[0]==5
        assert "cache_budget_mb" in {row[1] for row in c.execute("PRAGMA table_info(device)")}
    with sqlite3.connect(backups[0]) as c:
        assert c.execute("SELECT id FROM mediaasset").fetchone()[0]=='old-id'
    engine.dispose()


def test_v3_workflow_dynamic_combo():
    from app.comfy.projection_workflows import Graph
    g=Graph({"Save":{"input":{"required":{"codec":["COMFY_DYNAMICCOMBO_V3",{"options":[{"key":"auto","inputs":{}},{"key":"h264","inputs":{}}]}]}}}})
    g.add("save","Save",codec="auto")
    with pytest.raises(ValueError,match="Unavailable"):
        g.add("bad","Save",codec="unsupported")


def test_interpolation_part_boundaries_and_rounding(monkeypatch):
    from app.media.interpolation import boundaries
    monkeypatch.setattr(ffmpeg,"_run",lambda *args:"frame:1 pts:120 pts_time:5.005")
    fps=30000/1001
    spans=boundaries("test.mp4",20.02,fps)
    cut=round(5.005*fps)
    assert any(s["first"]+s["count"]==cut and not s["extra"] for s in spans)
    assert all(s["count"]<=round(fps*4) for s in spans)
    frames=sum(round((s["first"]+s["count"])/fps*60)-round(s["first"]/fps*60) for s in spans)
    assert frames==round(20.02*60)
    assert spans[-1]["extra"]==0
