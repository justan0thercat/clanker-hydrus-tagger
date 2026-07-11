---
license: gpl-3.0
library_name: onnxruntime
pipeline_tag: image-classification
tags:
- onnx
- hydrus
- autotagger
- anime
- manga
- danbooru
- not-for-all-audiences
---

# camie-tagger ONNX files for Hydrus tagging

This repo contains the ONNX inference files used by `clanker-hydrus-tagger` for `camie-tagger`.

It is meant to be dropped into a local model folder, not used as a training repo.

## Files

- `model.onnx`
- `info.json`
- `camie-tagger-v2-metadata.json`

## Folder layout

```text
model/camie-tagger/
  model.onnx
  info.json
  camie-tagger-v2-metadata.json
```

## Use

Copy the files into `model/camie-tagger/` inside `clanker-hydrus-tagger` and run `4_camie-tagger.bat`.

## Source

- Original project: `Camais03/camie-tagger`
- The ONNX metadata layout used here is compatible with `deepghs/camie_tagger_onnx`

## Notes

- `camie-tagger-v2-metadata.json` is required by the launcher.
- The filenames and folder layout are expected by the local runtime.
