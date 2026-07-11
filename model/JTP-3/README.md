---
license: apache-2.0
library_name: onnxruntime
pipeline_tag: image-classification
tags:
- onnx
- hydrus
- autotagger
- furry
- e621
- not-for-all-audiences
---

# JTP-3 ONNX files for Hydrus tagging

This repo contains the ONNX inference files used by `clanker-hydrus-tagger` for `JTP-3`.

It is meant to be dropped into a local model folder, not used as a training repo.

## Files

- `model.onnx`
- `info.json`
- `model-labels.csv`

## Folder layout

```text
model/JTP-3/
  model.onnx
  info.json
  model-labels.csv
```

## Use

Copy the files into `model/JTP-3/` inside `clanker-hydrus-tagger` and run `1_JTP-3.bat`.

## Source

- Original model family: `RedRocket/Hydra`
- This repo repackages the ONNX files and metadata for local Hydrus tagging

## Notes

- `info.json` contains runtime metadata used by the launcher.
- The filenames and folder layout are expected by the local runtime.
