"""Versioned local projection graphs, validated against the connected ComfyUI schema."""
VERSION = "projection-v5"


class Graph:
    def __init__(self, schema):
        self.schema, self.nodes = schema, {}

    def add(self, key, cls, **inputs):
        if cls not in self.schema:
            raise ValueError(f"Missing ComfyUI node: {cls}. Install/enable its local workflow dependencies.")
        spec = self.schema[cls].get("input", {})
        for name, rule in spec.get("required", {}).items():
            if name in inputs: continue
            opts = rule[1] if len(rule) > 1 else {}
            choices = opts.get("options", rule[0])
            if rule[0] == "COMFY_DYNAMICCOMBO_V3": choices = [o["key"] for o in choices]
            if "default" in opts: inputs[name] = opts["default"]
            elif isinstance(choices, list) and choices: inputs[name] = choices[0]
            else: raise ValueError(f"Missing required input {cls}.{name}; workflow version incompatible")
        for name, val in inputs.items():
            rule = {**spec.get("required", {}), **spec.get("optional", {})}.get(name)
            if rule is None: raise ValueError(f"Unsupported input {cls}.{name}")
            choices = (rule[1].get("options",rule[0]) if len(rule)>1 else rule[0])
            if rule[0] == "COMFY_DYNAMICCOMBO_V3": choices = [o["key"] for o in choices]
            if isinstance(choices, list) and not isinstance(val, list) and val not in choices and not ((cls, name) in (("LoadImage", "image"), ("LoadVideo", "file"))):
                raise ValueError(f"Unavailable model/option for {cls}.{name}: {val}")
        self.nodes[key] = {"class_type": cls, "inputs": inputs}
        return [key, 0]

    def model(self, cls, field, *needles):
        try:
            rule = self.schema[cls]["input"]["required"][field]
            choices = rule[1].get("options",rule[0]) if len(rule)>1 else rule[0]
        except KeyError: raise ValueError(f"Missing node/model registry: {cls}.{field}")
        for needle in needles:
            match = next((x for x in choices if needle.lower() in x.lower()), None)
            if match: return match
        raise ValueError(f"Missing model for {cls}: expected one of {', '.join(needles)}. Use the shared D: model store.")


def image_graph(schema, prompt, seed, image=None, orientation="vertical"):
    g = Graph(schema)
    model = g.add("model", "UNETLoader", unet_name=g.model("UNETLoader", "unet_name", "qwen_image_edit_2511_fp8", "Qwen-Image-Edit-2509_fp8"), weight_dtype="default")
    model = g.add("sampling", "ModelSamplingAuraFlow", model=model, shift=3.1)
    model = g.add("cfg_norm", "CFGNorm", model=model, strength=1.0)
    clip = g.add("clip", "CLIPLoader", clip_name=g.model("CLIPLoader", "clip_name", "qwen_2.5_vl_7b", "qwen2.5_vl_7b"), type="qwen_image", device="default")
    vae = g.add("vae", "VAELoader", vae_name=g.model("VAELoader", "vae_name", "qwen_image_vae"))
    refs = {}
    if image: refs["image1"] = g.add("reference", "LoadImage", image=image)
    positive = g.add("positive", "TextEncodeQwenImageEditPlus", clip=clip, vae=vae, prompt=prompt, **refs)
    # Both guidance branches need the same reference latents. Omitting them from
    # the negative branch distorts identity instead of guiding the requested edit.
    negative = g.add("negative", "TextEncodeQwenImageEditPlus", clip=clip, vae=vae, prompt="", **refs)
    if image:
        positive = g.add("positive_reference", "FluxKontextMultiReferenceLatentMethod", conditioning=positive, reference_latents_method="index_timestep_zero")
        negative = g.add("negative_reference", "FluxKontextMultiReferenceLatentMethod", conditioning=negative, reference_latents_method="index_timestep_zero")
    width, height = (1024,576) if orientation == "horizontal" else (768,1152)
    latent = g.add("latent", "VAEEncode", pixels=refs["image1"], vae=vae) if image else g.add("latent", "EmptySD3LatentImage", width=width, height=height, batch_size=1)
    sampled = g.add("sample", "KSampler", model=model, positive=positive, negative=negative, latent_image=latent, seed=seed, steps=20, cfg=4.0, sampler_name="euler", scheduler="simple", denoise=1.0)
    decoded = g.add("decode", "VAEDecode", samples=sampled, vae=vae)
    g.add("save", "SaveImage", images=decoded, filename_prefix="projector/storyboard")
    return g.nodes


def interpolation_graph(schema, filename, fps, multiplier):
    g = Graph(schema)
    video = g.add("load", "LoadVideo", file=filename)
    frames = g.add("components", "GetVideoComponents", video=video)
    frames = g.add("rife", "RIFE VFI", frames=frames, ckpt_name=g.model("RIFE VFI", "ckpt_name", "rife47.pth", "rife49.pth"), multiplier=multiplier, clear_cache_after_n_frames=10, fast_mode=True, ensemble=False, scale_factor=1.0)
    video = g.add("video", "CreateVideo", images=frames, fps=fps * multiplier)
    g.add("save", "SaveVideo", video=video, filename_prefix="projector/library-interpolation", format="mp4", codec="auto")
    return g.nodes


def video_graph(schema, prompt, seed, image, seconds, backend="ltx", orientation="vertical"):
    g = Graph(schema)
    start = g.add("image", "LoadImage", image=image)
    frames = 121 if seconds == 5 else 241
    if backend == "ltx":
        ckpt = g.model("CheckpointLoaderSimple", "ckpt_name", "ltx-2.3-22b-distilled-fp8", "ltx-2.3-22b-distilled")
        g.add("checkpoint", "CheckpointLoaderSimple", ckpt_name=ckpt)
        model, vae = ["checkpoint", 0], ["checkpoint", 2]
        clip = g.add("clip", "LTXAVTextEncoderLoader", text_encoder=g.model("LTXAVTextEncoderLoader", "text_encoder", "gemma_3_12B_it_fp8", "gemma_3_12B_it_fp4", "gemma"), ckpt_name=ckpt, device="default")
        audio_vae = g.add("audio_vae", "LTXVAudioVAELoader", ckpt_name=ckpt)
        positive = g.add("positive", "CLIPTextEncode", clip=clip, text=prompt)
        negative = g.add("negative", "CLIPTextEncode", clip=clip, text="camera motion, cuts, text, changing anatomy, watermark")
        g.add("conditioning", "LTXVConditioning", positive=positive, negative=negative, frame_rate=24.0)
        width, height = (768,448) if orientation == "horizontal" else (512,768)
        latent = g.add("latent", "EmptyLTXVLatentVideo", width=width, height=height, length=frames, batch_size=1)
        latent = g.add("condition_image", "LTXVImgToVideoConditionOnly", vae=vae, image=start, latent=latent, strength=0.85, bypass=False)
        audio_latent = g.add("audio_latent", "LTXVEmptyLatentAudio", audio_vae=audio_vae, frames_number=frames, frame_rate=24, batch_size=1)
        av = g.add("av", "LTXVConcatAVLatent", video_latent=latent, audio_latent=audio_latent)
        noise = g.add("noise", "RandomNoise", noise_seed=seed)
        guider = g.add("guider", "CFGGuider", model=model, positive=["conditioning", 0], negative=["conditioning", 1], cfg=1.0)
        sampler = g.add("sampler", "KSamplerSelect", sampler_name="euler")
        sigmas = g.add("sigmas", "ManualSigmas", sigmas="1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0")
        sampled = g.add("sample", "SamplerCustomAdvanced", noise=noise, guider=guider, sampler=sampler, sigmas=sigmas, latent_image=av)
        g.add("separate", "LTXVSeparateAVLatent", av_latent=sampled)
        decoded = g.add("decode", "VAEDecode", samples=["separate", 0], vae=vae)
        audio = g.add("audio", "LTXVAudioVAEDecode", samples=["separate", 1], audio_vae=audio_vae)
    else:
        model = g.add("model", "UNETLoader", unet_name=g.model("UNETLoader", "unet_name", "wan2.2_ti2v_5B"), weight_dtype="default")
        clip = g.add("clip", "CLIPLoader", clip_name=g.model("CLIPLoader", "clip_name", "umt5_xxl"), type="wan", device="default")
        vae = g.add("vae", "VAELoader", vae_name=g.model("VAELoader", "vae_name", "wan2.2_vae", "wan_2.2_vae"))
        pos = g.add("positive", "CLIPTextEncode", clip=clip, text=prompt)
        neg = g.add("negative", "CLIPTextEncode", clip=clip, text="camera movement, changing identity, cuts, text")
        width, height = (1280,704) if orientation == "horizontal" else (704,1280)
        g.add("condition", "Wan22ImageToVideoLatent", vae=vae, start_image=start, width=width, height=height, length=frames, batch_size=1)
        sampled = g.add("sample", "KSampler", model=model, positive=pos, negative=neg, latent_image=["condition", 0], seed=seed, steps=20, cfg=5.0, sampler_name="uni_pc", scheduler="simple", denoise=1.0)
        decoded = g.add("decode", "VAEDecode", samples=sampled, vae=vae)
        mma = g.add("mma", "MMAudioModelLoader", mmaudio_model=g.model("MMAudioModelLoader", "mmaudio_model", "large_44k"), base_precision="fp16")
        features = g.add("mma_features", "MMAudioFeatureUtilsLoader", vae_model=g.model("MMAudioFeatureUtilsLoader", "vae_model", "vae"), synchformer_model=g.model("MMAudioFeatureUtilsLoader", "synchformer_model", "synchformer"), clip_model=g.model("MMAudioFeatureUtilsLoader", "clip_model", "clip"), mode="44k", precision="fp16")
        audio = g.add("audio", "MMAudioSampler", mmaudio_model=mma, feature_utils=features, images=decoded, prompt=prompt, negative_prompt="music, speech", seed=seed, steps=25, cfg=4.5, duration=frames/24)
    native = g.add("native", "CreateVideo", images=decoded, fps=24.0, audio=audio)
    g.add("save_native", "SaveVideo", video=native, filename_prefix="projector/native24", format="mp4", codec="auto")
    interpolated = g.add("rife", "RIFE VFI", frames=decoded, ckpt_name=g.model("RIFE VFI", "ckpt_name", "rife47.pth", "rife49.pth", "rife"), multiplier=5, clear_cache_after_n_frames=10, fast_mode=True, ensemble=False, scale_factor=1.0)
    delivery = g.add("delivery", "CreateVideo", images=interpolated, fps=120.0, audio=audio)
    g.add("save_delivery", "SaveVideo", video=delivery, filename_prefix="projector/delivery120", format="mp4", codec="auto")
    return g.nodes
