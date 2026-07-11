# Publishing model repos on Hugging Face

This project expects these two model repos:

- `0xk1ru/jtp-3-onnx`
- `0xk1ru/camie-tagger-onnx`

Each repo should keep the files at the repo root, not inside an extra nested folder.

## Required files

### `0xk1ru/jtp-3-onnx`

- `model.onnx`
- `info.json`
- `model-labels.csv`
- `README.md`

### `0xk1ru/camie-tagger-onnx`

- `model.onnx`
- `info.json`
- `camie-tagger-v2-metadata.json`
- `README.md`

## Upload with `hf`

Upload the full folders:

```powershell
hf upload 0xk1ru/jtp-3-onnx .\model\JTP-3 .
hf upload 0xk1ru/camie-tagger-onnx .\model\camie-tagger .
```

Upload only the model cards:

```powershell
hf upload 0xk1ru/jtp-3-onnx .\model\JTP-3\README.md README.md
hf upload 0xk1ru/camie-tagger-onnx .\model\camie-tagger\README.md README.md
```

## Notes

- Keep the filenames unchanged. The launcher reads them from `info.json`.
- If you replace a model file, upload the matching metadata file too.
- Check the model card, license, and upstream attribution before making a repo public.
