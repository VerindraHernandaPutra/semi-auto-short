"""Voice cloning worker. Runs in its own Python environment (torch + chatterbox-tts), not the project's.

Usage: python -I voice_worker.py request.json
The request is a JSON file: {"reference": "ref.wav", "out": "voice.wav", "sentences": [...],
"exaggeration": 0.5, "cfg_weight": 0.5, "gap_s": 0.35}. Paths are relative to the working directory.
"""

import json
import sys


def main(request_path: str) -> int:
    with open(request_path, encoding="utf-8") as handle:
        request = json.load(handle)

    import torch
    import torchaudio as ta
    from chatterbox.tts import ChatterboxTTS

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"device: {device}", flush=True)
    model = ChatterboxTTS.from_pretrained(device=device)
    gap = torch.zeros(1, int(model.sr * float(request.get("gap_s", 0.35))))
    pieces = []
    for number, sentence in enumerate(request["sentences"], start=1):
        print(f"sentence {number}/{len(request['sentences'])}", flush=True)
        wav = model.generate(
            sentence,
            audio_prompt_path=request["reference"],
            exaggeration=float(request.get("exaggeration", 0.5)),
            cfg_weight=float(request.get("cfg_weight", 0.5)),
        )
        pieces += [wav.cpu(), gap]
    ta.save(request["out"], torch.cat(pieces, dim=1), model.sr)
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
