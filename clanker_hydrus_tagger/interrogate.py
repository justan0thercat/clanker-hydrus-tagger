import os
import json
import numpy as np
import cv2
import pandas as pd
from typing import Tuple, Dict, List
from PIL import Image
from pathlib import Path

from . import onnx_loader

# ----------------------------------------------------------------------
def _apply_sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _ensure_probabilities(output: np.ndarray) -> np.ndarray:
    """Return model output as probabilities without double-applying sigmoid."""
    finite_output = output[np.isfinite(output)]
    if finite_output.size and finite_output.min() >= 0.0 and finite_output.max() <= 1.0:
        return output
    return _apply_sigmoid(output)


def _static_output_size(model) -> int | None:
    size = model.get_outputs()[0].shape[1]
    return size if isinstance(size, int) else None


class WaifuDiffusionInterrogator:
    def __init__(self, name: str, model_file: str, tags_file: str, folder: str,
                 ratingsflag: bool, numberofratings: int, **kwargs) -> None:
        self.name = name
        self.model_file = model_file
        self.tags_file = tags_file
        self.folder = folder
        self.ratingsflag = ratingsflag
        self.numberofratings = numberofratings
        self.kwargs = kwargs

        self.model = None
        self.input_size = None
        self.patch_size = None
        self.input_mode = 'image'
        self.output_name = None
        self.candidate_output_name = None
        self.is_channels_first = False
        self.tags_list = []
        self.tag_to_category = {}
        self.rating_tags = []
        self.rating_indices = []
        self.repo_id = kwargs.get('repo_id')
        self.repo_revision = kwargs.get('repo_revision', 'main')
        # normalization mode from info.json; none keeps the old raw pixel behavior
        self.normalization = kwargs.get('normalization', None)
        self.color_order = kwargs.get('color_order', 'BGR')
        self.score_activation = kwargs.get('score_activation', 'auto')

    # --------------------------------------------------------------
    # metadata loading
    # --------------------------------------------------------------
    def _load_metadata_from_json(self, metadata_path: Path) -> bool:
        try:
            with open(metadata_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)

            # supports both flat metadata and the nested camie tag mapping format
            if 'idx_to_tag' in meta:
                idx_to_tag = meta['idx_to_tag']
                tag_to_category = meta.get('tag_to_category', {})
                rating_tags = meta.get('rating_tags', [])
            elif 'dataset_info' in meta and 'tag_mapping' in meta['dataset_info']:
                tag_mapping = meta['dataset_info']['tag_mapping']
                idx_to_tag = tag_mapping.get('idx_to_tag', {})
                tag_to_category = tag_mapping.get('tag_to_category', {})
                rating_tags = [tag for tag, cat in tag_to_category.items() if cat == 'rating']
            else:
                raise KeyError("unknown metadata structure")

            if not idx_to_tag:
                raise ValueError("idx_to_tag is empty")

            # build a tag list ordered by the model output index
            max_idx = max(int(k) for k in idx_to_tag.keys())
            self.tags_list = [None] * (max_idx + 1)
            for idx_str, tag in idx_to_tag.items():
                self.tags_list[int(idx_str)] = tag

            self.tag_to_category = tag_to_category
            self.rating_tags = rating_tags
            self.rating_indices = [
                i for i, tag in enumerate(self.tags_list) if tag in self.rating_tags
            ]

            if self.rating_tags:
                self.ratingsflag = True
                self.numberofratings = len(self.rating_tags)

            print(f"Loaded metadata: {len(self.tags_list)} tags, {len(self.rating_tags)} rating tags, {len(self.tag_to_category)} categories")
            return True
        except Exception as e:
            print(f"Warning: could not load metadata.json: {e}")
            return False

    def _load_tags_from_csv(self, csv_path: Path) -> bool:
        try:
            df = pd.read_csv(csv_path)
            num_outputs = _static_output_size(self.model)
            if num_outputs is not None and len(df) != num_outputs:
                print(f"Adjusting CSV rows: {len(df)} -> {num_outputs}")
                if len(df) > num_outputs:
                    df = df.iloc[:num_outputs]
                else:
                    missing = num_outputs - len(df)
                    dummy = pd.DataFrame([['unknown']] * missing, columns=['name'])
                    if 'category' in df.columns:
                        dummy['category'] = 0
                    df = pd.concat([df, dummy], ignore_index=True)
            self.tags_list = df['name'].tolist()
            if 'category' in df.columns:
                self.tag_to_category = dict(zip(df['name'], df['category']))
            else:
                self.tag_to_category = {}
            # csv models may mark rating tags through a category column
            self.rating_tags = [tag for tag, cat in self.tag_to_category.items() if cat == 'rating']
            self.rating_indices = [
                i for i, tag in enumerate(self.tags_list) if tag in self.rating_tags
            ]
            if self.rating_tags:
                self.ratingsflag = True
                self.numberofratings = len(self.rating_tags)
            print(f"Loaded CSV: {len(self.tags_list)} tags")
            return True
        except Exception as e:
            print(f"Failed to load CSV: {e}")
            return False

    def findpaths(self):
        model_path = Path('./model/' + self.folder + '/' + self.model_file)
        metadata_path = model_path.parent / 'camie-tagger-v2-metadata.json'
        csv_path = model_path.parent / self.tags_file
        tags_path = metadata_path if metadata_path.exists() else csv_path if csv_path.exists() else None
        return model_path, tags_path

    # --------------------------------------------------------------
    # onnx loading
    # --------------------------------------------------------------
    def load(self, cpu) -> None:
        model_dir = Path('./model/' + self.folder)
        onnx_loader.ensure_huggingface_files(
            model_dir,
            self.repo_id,
            [self.model_file, self.tags_file],
            revision=self.repo_revision,
            model_name=self.name,
        )

        model_path, tags_path = self.findpaths()
        if not model_path.exists():
            raise FileNotFoundError(f"Model not found: {model_path}")

        if not onnx_loader.is_installed('onnxruntime'):
            package = os.environ.get('ONNXRUNTIME_PACKAGE', 'onnxruntime-gpu')
            onnx_loader.run_pip(f'install {package}', 'onnxruntime')

        from onnxruntime import InferenceSession
        import onnxruntime as ort
        ort.set_default_logger_severity(3)

        providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
        if cpu:
            providers.pop(0)
        cuda_options = {
            'cudnn_conv_algo_search': 'HEURISTIC',
            'arena_extend_strategy': 'kSameAsRequested',
        }
        if not cpu and 'CUDAExecutionProvider' in providers:
            self.model = InferenceSession(
                str(model_path),
                providers=[('CUDAExecutionProvider', cuda_options), 'CPUExecutionProvider']
            )
        else:
            self.model = InferenceSession(str(model_path), providers=providers)

        print(f'Loaded {self.name} model from {model_path}')

        input_shape = self.model.get_inputs()[0].shape
        output_size = _static_output_size(self.model)
        outputs = self.model.get_outputs()
        refined_output = next((out.name for out in outputs if out.name == 'refined_predictions'), None)
        self.output_name = refined_output or outputs[0].name
        candidate_output = next((out.name for out in outputs if out.name == 'selected_candidates'), None)
        self.candidate_output_name = candidate_output

        if len(input_shape) == 3 and isinstance(input_shape[1], int) and isinstance(input_shape[2], int):
            patch_area = input_shape[2] // 3
            patch_size = int(np.sqrt(patch_area))
            grid_size = int(np.sqrt(input_shape[1]))
            if patch_size * patch_size * 3 == input_shape[2] and grid_size * grid_size == input_shape[1]:
                self.input_mode = 'patches'
                self.patch_size = patch_size
                self.input_size = grid_size * patch_size

        if self.input_mode == 'image':
            self.is_channels_first = (len(input_shape) == 4 and input_shape[1] in (1, 3))
            for dim in input_shape:
                if isinstance(dim, int) and dim > 100:
                    self.input_size = dim
                    break
        if self.input_size is None:
            self.input_size = 512
        print(f"Model input: mode={self.input_mode}, channels_first={self.is_channels_first}, size={self.input_size}")

        if tags_path and tags_path.suffix == '.json':
            use_meta = self._load_metadata_from_json(tags_path)
        else:
            use_meta = False

        if not use_meta and tags_path and tags_path.suffix == '.csv':
            self._load_tags_from_csv(tags_path)

        if not self.tags_list:
            raise RuntimeError("No tag metadata found (neither metadata.json nor CSV)")
        if output_size is not None and len(self.tags_list) != output_size:
            print(f"Adjusting metadata tags: {len(self.tags_list)} -> {output_size}")
            self.tags_list = self.tags_list[:output_size]
            self.rating_tags = [tag for tag in self.rating_tags if tag in self.tags_list]
            self.rating_indices = [
                i for i, tag in enumerate(self.tags_list) if tag in self.rating_tags
            ]

    # --------------------------------------------------------------
    # image preprocessing
    # --------------------------------------------------------------
    def _preprocess_image(self, image: Image.Image) -> np.ndarray:
        # flatten alpha onto white, same as most wd tagger forks do
        if image.mode == 'RGBA':
            white_bg = Image.new('RGBA', image.size, (255,255,255,255))
            white_bg.paste(image, mask=image.split()[-1])
            image = white_bg.convert('RGB')
        else:
            image = image.convert('RGB')

        # convert to numpy and flip rgb to bgr for the older wd-style models
        img_np = np.asarray(image, dtype=np.uint8)
        if self.color_order == 'BGR':
            img_np = img_np[:, :, ::-1]

        # pad to square before resizing so the model sees the whole image
        old_size = img_np.shape[:2]
        desired_size = max(old_size)
        desired_size = max(desired_size, self.input_size)
        
        delta_w = desired_size - old_size[1]
        delta_h = desired_size - old_size[0]
        top, bottom = delta_h // 2, delta_h - (delta_h // 2)
        left, right = delta_w // 2, delta_w - (delta_w // 2)
        img_np = cv2.copyMakeBorder(
            img_np, top, bottom, left, right, cv2.BORDER_CONSTANT, value=[255, 255, 255]
        )

        # resize with a downscale/upscale split to keep small images decent
        if img_np.shape[0] > self.input_size:
            img_np = cv2.resize(img_np, (self.input_size, self.input_size), interpolation=cv2.INTER_AREA)
        elif img_np.shape[0] < self.input_size:
            img_np = cv2.resize(img_np, (self.input_size, self.input_size), interpolation=cv2.INTER_CUBIC)

        img_np = img_np.astype(np.float32)

        # normalization depends on the model family
        if self.normalization == 'imagenet':
            # imagenet normalization
            img_np = img_np / 255.0
            mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
            std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
            img_np = (img_np - mean) / std
        elif self.normalization == 'range_-1_1':
            # jtp/camie style normalization
            img_np = (img_np / 127.5) - 1.0
        # otherwise keep raw pixels, which is the original wd behavior

        # channels-first models want nchw instead of nhwc
        if self.is_channels_first:
            img_np = np.transpose(img_np, (2, 0, 1))
        
        img_np = np.expand_dims(img_np, axis=0)
        return img_np.astype(np.float32)

    def _preprocess_patches(self, image: Image.Image) -> np.ndarray:
        if image.mode == 'RGBA':
            white_bg = Image.new('RGBA', image.size, (255,255,255,255))
            white_bg.paste(image, mask=image.split()[-1])
            image = white_bg.convert('RGB')
        else:
            image = image.convert('RGB')

        img_np = np.asarray(image, dtype=np.uint8)
        old_size = img_np.shape[:2]
        desired_size = max(old_size)
        delta_w = desired_size - old_size[1]
        delta_h = desired_size - old_size[0]
        top, bottom = delta_h // 2, delta_h - (delta_h // 2)
        left, right = delta_w // 2, delta_w - (delta_w // 2)
        img_np = cv2.copyMakeBorder(
            img_np, top, bottom, left, right, cv2.BORDER_CONSTANT, value=[255, 255, 255]
        )
        img_np = cv2.resize(img_np, (self.input_size, self.input_size), interpolation=cv2.INTER_AREA)

        patch_size = self.patch_size
        patches = img_np.reshape(
            self.input_size // patch_size,
            patch_size,
            self.input_size // patch_size,
            patch_size,
            3,
        )
        patches = patches.transpose(0, 2, 1, 3, 4).reshape(-1, patch_size * patch_size * 3)
        patches = patches.astype(np.float32)
        if self.normalization == 'range_-1_1':
            patches = (patches / 127.5) - 1.0
        elif self.normalization == '01':
            patches = patches / 255.0
        return np.expand_dims(patches, axis=0)

    def _preprocess(self, image: Image.Image) -> np.ndarray:
        if self.input_mode == 'patches':
            return self._preprocess_patches(image)
        return self._preprocess_image(image)

    # --------------------------------------------------------------
    # single and batched inference
    # --------------------------------------------------------------
    def _run_model(self, tensors: List[np.ndarray]) -> np.ndarray:
        """run a list of preprocessed tensors and return scores as (batch, num_tags)."""
        if len(tensors) == 1:
            input_tensor = tensors[0]
        else:
            input_tensor = np.concatenate(tensors, axis=0)

        model_inputs = self.model.get_inputs()
        input_feed = {model_inputs[0].name: input_tensor}
        for model_input in model_inputs[1:]:
            if model_input.name == 'valid':
                input_feed[model_input.name] = np.ones(input_tensor.shape[:2], dtype=bool)
            else:
                raise RuntimeError(f"Unsupported model input: {model_input.name}")
        output_names = [self.output_name]
        if self.candidate_output_name:
            output_names.append(self.candidate_output_name)
        outputs = self.model.run(output_names, input_feed)
        scores = outputs[0]

        if self.candidate_output_name:
            candidates = outputs[1]
            filtered_scores = np.full_like(scores, -np.inf, dtype=np.float32)
            for row_idx, row_candidates in enumerate(candidates):
                valid_indices = row_candidates[
                    (row_candidates >= 0) & (row_candidates < scores.shape[1])
                ].astype(np.int64)
                filtered_scores[row_idx, valid_indices] = scores[row_idx, valid_indices]
                for rating_idx in self.rating_indices:
                    if rating_idx < scores.shape[1]:
                        filtered_scores[row_idx, rating_idx] = scores[row_idx, rating_idx]
            scores = filtered_scores

        if self.score_activation == 'none':
            return scores
        if self.score_activation == 'sigmoid':
            return _apply_sigmoid(scores)
        return _ensure_probabilities(scores)

    def _probs_to_ratings_and_tags(self, probs: np.ndarray) -> Tuple[Dict[str, float], Dict[str, float]]:
        """split a 1d score array into ratings and normal tags."""
        ratings = {}
        tags_dict = {}

        if self.ratingsflag:
            if self.rating_tags:   # camie mode, or any csv/json model with explicit rating tags
                # ratings first
                for idx in self.rating_indices:
                    if idx >= len(probs):
                        continue
                    ratings[self.tags_list[idx]] = float(probs[idx])
                # normal tags after that, skipping ratings and noisy service categories
                for idx, tag in enumerate(self.tags_list[:len(probs)]):
                    if tag in self.rating_tags:
                        continue
                    prob = float(probs[idx])
                    category = self.tag_to_category.get(tag, '')
                    # these categories are useful internally but not great as hydrus tags
                    if category in ('year', 'meta'):
                        continue
                    # keep filtering in __main__.py so cli options stay in one place
                    tags_dict[tag] = prob
            else:                  # classic wd layout: ratings are the first n outputs
                n = self.numberofratings
                for i in range(min(n, len(self.tags_list))):
                    ratings[self.tags_list[i]] = float(probs[i])
                for i in range(n, min(len(self.tags_list), len(probs))):
                    tags_dict[self.tags_list[i]] = float(probs[i])
        else:
            # no rating head, just normal tags
            for i, tag in enumerate(self.tags_list[:len(probs)]):
                tags_dict[tag] = float(probs[i])
        return ratings, tags_dict

    def interrogate(self, image: Image.Image) -> Tuple[Dict[str, float], Dict[str, float]]:
        tensor = self._preprocess(image)
        probs = self._run_model([tensor])[0]
        return self._probs_to_ratings_and_tags(probs)

    def interrogate_batch(self, images: List[Image.Image]) -> List[Tuple[Dict[str, float], Dict[str, float]]]:
        tensors = [self._preprocess(img) for img in images]
        batch_probs = self._run_model(tensors)   # (batch, num_tags)
        results = []
        for probs in batch_probs:
            results.append(self._probs_to_ratings_and_tags(probs))
        return results
