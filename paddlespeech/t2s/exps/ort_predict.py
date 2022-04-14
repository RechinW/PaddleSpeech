# Copyright (c) 2022 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import argparse
from pathlib import Path

import jsonlines
import numpy as np
import onnxruntime as ort
import soundfile as sf
from timer import timer

from paddlespeech.t2s.exps.syn_utils import get_test_dataset
from paddlespeech.t2s.utils import str2bool


def get_sess(args, filed='am'):
    full_name = ''
    if filed == 'am':
        full_name = args.am
    elif filed == 'voc':
        full_name = args.voc
    model_dir = str(Path(args.inference_dir) / (full_name + ".onnx"))
    sess_options = ort.SessionOptions()
    sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    sess_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL

    if args.device == "gpu":
        # fastspeech2/mb_melgan can't use trt now!
        if args.use_trt:
            providers = ['TensorrtExecutionProvider']
        else:
            providers = ['CUDAExecutionProvider']
    elif args.device == "cpu":
        providers = ['CPUExecutionProvider']
    sess_options.intra_op_num_threads = args.cpu_threads
    sess = ort.InferenceSession(
        model_dir, providers=providers, sess_options=sess_options)
    return sess


def ort_predict(args):
    # construct dataset for evaluation
    with jsonlines.open(args.test_metadata, 'r') as reader:
        test_metadata = list(reader)
    am_name = args.am[:args.am.rindex('_')]
    am_dataset = args.am[args.am.rindex('_') + 1:]
    test_dataset = get_test_dataset(args, test_metadata, am_name, am_dataset)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    fs = 24000 if am_dataset != 'ljspeech' else 22050

    # am
    am_sess = get_sess(args, filed='am')

    # vocoder
    voc_sess = get_sess(args, filed='voc')

    # am warmup
    for T in [27, 38, 54]:
        am_input_feed = {}
        if am_name == 'fastspeech2':
            phone_ids = np.random.randint(1, 266, size=(T, ))
            am_input_feed.update({'text': phone_ids})
        elif am_name == 'speedyspeech':
            phone_ids = np.random.randint(1, 92, size=(T, ))
            tone_ids = np.random.randint(1, 5, size=(T, ))
            am_input_feed.update({'phones': phone_ids, 'tones': tone_ids})
        am_sess.run(None, input_feed=am_input_feed)

    # voc warmup
    for T in [227, 308, 544]:
        data = np.random.rand(T, 80).astype("float32")
        voc_sess.run(None, {"logmel": data})
    print("warm up done!")

    N = 0
    T = 0
    am_input_feed = {}
    for example in test_dataset:
        utt_id = example['utt_id']
        if am_name == 'fastspeech2':
            phone_ids = example["text"]
            am_input_feed.update({'text': phone_ids})
        elif am_name == 'speedyspeech':
            phone_ids = example["phones"]
            tone_ids = example["tones"]
            am_input_feed.update({'phones': phone_ids, 'tones': tone_ids})
        with timer() as t:
            mel = am_sess.run(output_names=None, input_feed=am_input_feed)
            mel = mel[0]
            wav = voc_sess.run(output_names=None, input_feed={'logmel': mel})
            N += len(wav[0])
            T += t.elapse
            speed = len(wav[0]) / t.elapse
            rtf = fs / speed
        sf.write(
            str(output_dir / (utt_id + ".wav")),
            np.array(wav)[0],
            samplerate=fs)
        print(
            f"{utt_id}, mel: {mel.shape}, wave: {len(wav[0])}, time: {t.elapse}s, Hz: {speed}, RTF: {rtf}."
        )
    print(f"generation speed: {N / T}Hz, RTF: {fs / (N / T) }")


def parse_args():
    parser = argparse.ArgumentParser(description="Infernce with onnxruntime.")
    # acoustic model
    parser.add_argument(
        '--am',
        type=str,
        default='fastspeech2_csmsc',
        choices=['fastspeech2_csmsc', 'speedyspeech_csmsc'],
        help='Choose acoustic model type of tts task.')

    # voc
    parser.add_argument(
        '--voc',
        type=str,
        default='hifigan_csmsc',
        choices=['hifigan_csmsc', 'mb_melgan_csmsc'],
        help='Choose vocoder type of tts task.')
    # other
    parser.add_argument(
        "--inference_dir", type=str, help="dir to save inference models")
    parser.add_argument("--test_metadata", type=str, help="test metadata.")
    parser.add_argument("--output_dir", type=str, help="output dir")

    # inference
    parser.add_argument(
        "--use_trt",
        type=str2bool,
        default=False,
        help="Whether to use inference engin TensorRT.", )

    parser.add_argument(
        "--device",
        default="gpu",
        choices=["gpu", "cpu"],
        help="Device selected for inference.", )
    parser.add_argument('--cpu_threads', type=int, default=1)

    args, _ = parser.parse_known_args()
    return args


def main():
    args = parse_args()

    ort_predict(args)


if __name__ == "__main__":
    main()
