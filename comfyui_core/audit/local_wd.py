from __future__ import annotations

import asyncio
import logging
from typing import Any

from PIL import Image

from ..exceptions import AuditError
from ..settings import CoreSettings
from .base import AuditResult, BaseAuditor, prepare_audit_image, rating_is_nsfw, resolve_audit_level

logger = logging.getLogger(__name__)


def hf_repo_id(repo_id: str) -> str:
    """The shipped default names the model without its owner, which the Hub rejects."""
    return repo_id if "/" in repo_id else f"SmilingWolf/{repo_id}"


class LocalWDAuditor(BaseAuditor):
    """WaifuDiffusion tagger run locally with onnxruntime (loaded on first use).

    Needs ``onnxruntime pandas numpy huggingface_hub``, which are optional
    dependencies.
    """

    def __init__(self, settings: CoreSettings) -> None:
        self.settings = settings
        self._interrogator: Any = None
        self._load_lock = asyncio.Lock()

    def _ensure_loaded(self) -> Any:
        if self._interrogator is not None:
            return self._interrogator

        try:
            from onnxruntime import InferenceSession
            import pandas as pd
            import numpy as np
            from huggingface_hub import hf_hub_download
        except ImportError as e:
            logger.error("Local WD14 audit dependencies missing: %s. "
                         "pip install onnxruntime pandas numpy huggingface_hub", e)
            raise

        wd_cfg = self.settings.audit.wd_model
        repo_id = hf_repo_id(wd_cfg["repo_id"])
        logger.info("Loading local WD14 model %s", repo_id)
        model_path = hf_hub_download(repo_id=repo_id, revision=wd_cfg.get("revision", "v2.0"),
                                     filename=wd_cfg.get("model_path", "model.onnx"))
        tags_path = hf_hub_download(repo_id=repo_id, revision=wd_cfg.get("revision", "v2.0"),
                                    filename=wd_cfg.get("tags_path", "selected_tags.csv"))

        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"] if self.settings.audit.gpu \
            else ["CPUExecutionProvider"]
        session = InferenceSession(str(model_path), providers=providers)
        tags_df = pd.read_csv(tags_path)

        class WDWrapper:
            def __init__(self, sess, tags):
                self.sess = sess
                self.tags = tags

            def run_infer(self, pil_img: Image.Image) -> tuple[dict[str, float], dict[str, float]]:
                _, height, _, _ = self.sess.get_inputs()[0].shape
                img = pil_img.convert("RGBA")
                new_img = Image.new("RGBA", img.size, "WHITE")
                new_img.paste(img, mask=img)
                arr = np.asarray(new_img.convert("RGB"))[:, :, ::-1]

                old_size = arr.shape[:2]
                ratio = float(height) / max(old_size)
                new_size = tuple(int(x * ratio) for x in old_size)
                im_pil = Image.fromarray(arr).resize(new_size, Image.LANCZOS)
                canvas = Image.new("RGB", (height, height))
                canvas.paste(im_pil, ((height - new_size[0]) // 2, (height - new_size[1]) // 2))

                in_arr = np.expand_dims(np.array(canvas).astype(np.float32), 0)
                in_name = self.sess.get_inputs()[0].name
                out_name = self.sess.get_outputs()[0].name
                confidents = self.sess.run([out_name], {in_name: in_arr})[0]

                tags_col = self.tags[["name"]].copy()
                tags_col["confidents"] = confidents[0]
                return dict(tags_col[:4].values), dict(tags_col[4:].values)

        self._interrogator = WDWrapper(session, tags_df)
        logger.info("Local WD14 model ready")
        return self._interrogator

    async def audit(self, image_bytes: bytes, group_id: str = "") -> AuditResult:
        audit_info: AuditResult = {"is_nsfw": False, "message": "", "tags": {}}
        try:
            async with self._load_lock:
                interrogator = await asyncio.to_thread(self._ensure_loaded)
        except Exception as e:
            raise AuditError(f"本地 WD14 加载失败: {e}")

        try:
            _, pil_img = prepare_audit_image(image_bytes, self.settings.audit.downscale)
            ratings, _ = await asyncio.to_thread(interrogator.run_infer, pil_img)
        except Exception as e:
            raise AuditError(f"WD14 推理失败: {e}")

        top_category, top_score = max(ratings.items(), key=lambda x: x[1])
        level = resolve_audit_level(self.settings.audit.level, self.settings.group_config, group_id)
        audit_info["tags"] = ratings
        audit_info["message"] = f"WD14 本地审核判定: {top_category} ({top_score * 100:.1f}%)"
        audit_info["is_nsfw"] = rating_is_nsfw(top_category, level)
        return audit_info
