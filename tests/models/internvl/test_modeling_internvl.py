# coding=utf-8
# Copyright 2024 The Qwen team, Alibaba Group and The HuggingFace Inc. team. All rights reserved.
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
"""Testing suite for the PyTorch GotOcr2 model."""

import unittest
from functools import partial
from io import BytesIO

import requests

from transformers import (
    AutoProcessor,
    BitsAndBytesConfig,
    InternVLConfig,
    is_torch_available,
    is_vision_available,
)
from transformers.testing_utils import (
    cleanup,
    require_bitsandbytes,
    require_torch,
    require_torch_gpu,
    slow,
    torch_device,
)

from ...generation.test_utils import GenerationTesterMixin
from ...test_configuration_common import ConfigTester
from ...test_modeling_common import ModelTesterMixin, _config_zero_init, floats_tensor, ids_tensor
from ...test_pipeline_mixin import PipelineTesterMixin


if is_torch_available():
    import torch

    from transformers import (
        InternVLForConditionalGeneration,
    )


if is_vision_available():
    from PIL import Image


class InternVLVisionText2TextModelTester:
    def __init__(
        self,
        parent,
        batch_size=3,
        seq_length=7,
        image_seq_length=64,
        vision_feature_layer=-1,
        ignore_index=-100,
        bos_token_id=0,
        eos_token_id=0,
        pad_token_id=0,
        image_token_index=1,
        num_channels=3,
        image_size=64,
        model_type="internvl",
        is_training=True,
        text_config={
            "model_type": "qwen2",
            "vocab_size": 99,
            "hidden_size": 128,
            "intermediate_size": 37,
            "num_hidden_layers": 4,
            "num_attention_heads": 4,
            "num_key_value_heads": 2,
            "output_channels": 64,
            "hidden_act": "silu",
            "max_position_embeddings": 512,
            "rope_theta": 10000,
            "mlp_ratio": 4,
            "tie_word_embeddings": True,
            "bos_token_id": 0,
            "eos_token_id": 0,
            "pad_token_id": 0,
        },
        vision_config={
            "hidden_size": 32,
            "num_hidden_layers": 2,
            "num_attention_heads": 4,
            "intermediate_size": 128,
            "image_size": 64,
            "patch_size": 4,
            "num_channels": 3,
            "hidden_act": "quick_gelu",
            "use_absolute_position_embeddings": True,
        },
    ):
        self.parent = parent
        self.ignore_index = ignore_index
        self.bos_token_id = bos_token_id
        self.eos_token_id = eos_token_id
        self.pad_token_id = pad_token_id
        self.image_token_index = image_token_index
        self.model_type = model_type
        self.text_config = text_config
        self.vision_config = vision_config
        self.batch_size = batch_size
        self.vision_feature_layer = vision_feature_layer
        self.is_training = is_training
        self.image_seq_length = image_seq_length
        self.num_channels = num_channels
        self.image_size = image_size
        self.seq_length = seq_length + image_seq_length

        self.num_hidden_layers = text_config["num_hidden_layers"]
        self.vocab_size = text_config["vocab_size"]
        self.hidden_size = text_config["hidden_size"]
        self.num_attention_heads = text_config["num_attention_heads"]

    def get_config(self):
        return InternVLConfig(
            text_config=self.text_config,
            vision_config=self.vision_config,
            model_type=self.model_type,
            bos_token_id=self.bos_token_id,
            eos_token_id=self.eos_token_id,
            pad_token_id=self.pad_token_id,
            image_token_index=self.image_token_index,
            image_seq_length=self.image_seq_length,
            vision_feature_layer=self.vision_feature_layer,
        )

    def prepare_config_and_inputs(self):
        config = self.get_config()
        pixel_values = floats_tensor([self.batch_size, self.num_channels, self.image_size, self.image_size])

        return config, pixel_values

    def prepare_config_and_inputs_for_common(self):
        config_and_inputs = self.prepare_config_and_inputs()
        config, pixel_values = config_and_inputs
        input_ids = ids_tensor([self.batch_size, self.seq_length], self.vocab_size)
        attention_mask = torch.ones(input_ids.shape, dtype=torch.long, device=torch_device)

        # input_ids[:, -1] = self.pad_token_id
        input_ids[input_ids == self.image_token_index] = self.pad_token_id
        input_ids[:, : self.image_seq_length] = self.image_token_index

        inputs_dict = {
            "pixel_values": pixel_values,
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }
        return config, inputs_dict

    def create_and_check_model_fp16_forward(self, config, input_ids, pixel_values, attention_mask):
        model = InternVLForConditionalGeneration(config=config)
        model.to(torch_device)
        model.half()
        model.eval()
        logits = model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            pixel_values=pixel_values.to(torch.bfloat16),
            return_dict=True,
        )["logits"]
        self.parent.assertFalse(torch.isnan(logits).any().item())

    def create_and_check_model_fp16_autocast_forward(self, config, input_ids, pixel_values, attention_mask):
        config.torch_dtype = torch.float16
        model = InternVLForConditionalGeneration(config=config)
        model.to(torch_device)
        model.eval()
        with torch.autocast(device_type="cuda", dtype=torch.float16):
            logits = model(
                input_ids=input_ids,
                attention_mask=attention_mask,
                pixel_values=pixel_values.to(torch.bfloat16),
                return_dict=True,
            )["logits"]
        self.parent.assertFalse(torch.isnan(logits).any().item())


@require_torch
class InternVLModelTest(ModelTesterMixin, GenerationTesterMixin, PipelineTesterMixin, unittest.TestCase):
    all_model_classes = (InternVLForConditionalGeneration,) if is_torch_available() else ()
    all_generative_model_classes = (InternVLForConditionalGeneration,) if is_torch_available() else ()
    pipeline_model_mapping = (
        {
            "image-text-to-text": InternVLForConditionalGeneration,
        }
        if is_torch_available()
        else {}
    )
    test_headmasking = False
    test_pruning = False

    def setUp(self):
        self.model_tester = InternVLVisionText2TextModelTester(self)
        self.config_tester = ConfigTester(self, config_class=InternVLConfig, has_text_modality=False)

    def test_config(self):
        self.config_tester.run_common_tests()

    def test_initialization(self):
        config, inputs_dict = self.model_tester.prepare_config_and_inputs_for_common()

        configs_no_init = _config_zero_init(config)
        for model_class in self.all_model_classes:
            model = model_class(config=configs_no_init)
            for name, param in model.named_parameters():
                if param.requires_grad:
                    self.assertIn(
                        ((param.data.mean() * 1e9).round() / 1e9).item(),
                        [0.0, 1.0],
                        msg=f"Parameter {name} of model {model_class} seems not properly initialized",
                    )

    # overwrite inputs_embeds tests because we need to delete "pixel values" for LVLMs
    def test_inputs_embeds(self):
        config, inputs_dict = self.model_tester.prepare_config_and_inputs_for_common()

        for model_class in self.all_model_classes:
            model = model_class(config)
            model.to(torch_device)
            model.eval()

            inputs = self._prepare_for_class(inputs_dict, model_class)

            input_ids = inputs["input_ids"]
            del inputs["input_ids"]
            del inputs["pixel_values"]

            wte = model.get_input_embeddings()
            inputs["inputs_embeds"] = wte(input_ids)

            with torch.no_grad():
                model(**inputs)

    # overwrite inputs_embeds tests because we need to delete "pixel values" for LVLMs
    # while some other models require pixel_values to be present
    def test_inputs_embeds_matches_input_ids(self):
        config, inputs_dict = self.model_tester.prepare_config_and_inputs_for_common()

        for model_class in self.all_model_classes:
            model = model_class(config)
            model.to(torch_device)
            model.eval()

            inputs = self._prepare_for_class(inputs_dict, model_class)
            input_ids = inputs["input_ids"]
            del inputs["input_ids"]
            del inputs["pixel_values"]

            inputs_embeds = model.get_input_embeddings()(input_ids)

            with torch.no_grad():
                out_ids = model(input_ids=input_ids, **inputs)[0]
                out_embeds = model(inputs_embeds=inputs_embeds, **inputs)[0]
            torch.testing.assert_close(out_embeds, out_ids)

    @unittest.skip(reason="Compile not yet supported because in LLava models")
    def test_sdpa_can_compile_dynamic(self):
        pass

    @unittest.skip("FlashAttention only support fp16 and bf16 data type")
    def test_flash_attn_2_fp32_ln(self):
        pass

    @unittest.skip("Qwen2 flash attention does not support right padding")
    def test_flash_attn_2_inference_equivalence_right_padding(self):
        pass


@require_torch
class InternVLQwen2IntegrationTest(unittest.TestCase):
    def setUp(self):
        self.small_model_checkpoint = "../InternVLTest-1B"
        self.medium_model_checkpoint = "../InternVLTest-4B"

    def tearDown(self):
        cleanup(torch_device, gc_collect=True)

    @slow
    @require_torch_gpu
    def test_qwen2_small_model_integration_generate(self):
        processor = AutoProcessor.from_pretrained(self.small_model_checkpoint)
        model = InternVLForConditionalGeneration.from_pretrained(
            self.small_model_checkpoint, device_map=torch_device, torch_dtype=torch.bfloat16
        )
        url = "http://images.cocodataset.org/val2017/000000039769.jpg"
        image = Image.open(requests.get(url, stream=True).raw)

        prompt = "<|im_start|>user\n<image>\nPlease describe the image explicitly.<|im_end|>\n<|im_start|>assistant\n"
        inputs = processor(images=image, text=prompt, return_tensors="pt").to(torch_device)
        with torch.no_grad():
            generate_ids = model.generate(**inputs, max_new_tokens=20, do_sample=False)
            decoded_output = processor.decode(
                generate_ids[0, inputs["input_ids"].shape[1] :], skip_special_tokens=True
            )
        expected_output = "The image shows two cats lying on a pink couch. One cat is on the left side of the"
        self.assertEqual(decoded_output, expected_output)

    @slow
    @require_torch_gpu
    def test_qwen2_small_model_integration_forward(self):
        processor = AutoProcessor.from_pretrained(self.small_model_checkpoint)
        model = InternVLForConditionalGeneration.from_pretrained(
            self.small_model_checkpoint, device_map=torch_device, torch_dtype=torch.bfloat16
        )
        url = "http://images.cocodataset.org/val2017/000000039769.jpg"
        image = Image.open(requests.get(url, stream=True).raw)

        prompt = "<|im_start|>user\n<image>\nPlease describe the image explicitly.<|im_end|>\n<|im_start|>assistant\n"
        inputs = processor(images=image, text=prompt, return_tensors="pt").to(torch_device)

        # Forward
        with torch.inference_mode():
            output = model(**inputs)

        actual_logits = output.logits[0, -1, :5].cpu()
        expected_logits = torch.tensor([11.5000, 9.8750, 14.0625, 9.8125, 8.2500], dtype=torch.bfloat16)
        self.assertTrue(
            torch.allclose(actual_logits, expected_logits, atol=0.1),
            f"Actual logits: {actual_logits}"
            f"\nExpected logits: {expected_logits}"
            f"\nDifference: {torch.abs(actual_logits - expected_logits)}",
        )

    @slow
    @require_torch_gpu
    def test_qwen2_small_model_integration_generate_text_only(self):
        processor = AutoProcessor.from_pretrained(self.small_model_checkpoint)
        model = InternVLForConditionalGeneration.from_pretrained(
            self.small_model_checkpoint, device_map=torch_device, torch_dtype=torch.bfloat16
        )
        prompt = "<|im_start|>user\nWrite a haiku<|im_end|>\n<|im_start|>assistant\n"
        inputs = processor(text=prompt, return_tensors="pt").to(torch_device)
        with torch.no_grad():
            generate_ids = model.generate(**inputs, max_new_tokens=200, do_sample=False)
            decoded_output = processor.decode(
                generate_ids[0, inputs["input_ids"].shape[1] :], skip_special_tokens=True
            )
        expected_output = "Whispers of dawn,\nLeaves rustle in the breeze,\nNew day begins."
        self.assertEqual(decoded_output, expected_output)

    @slow
    @require_torch_gpu
    def test_qwen2_small_model_integration_generate_chat_template(self):
        processor = AutoProcessor.from_pretrained(self.small_model_checkpoint)
        model = InternVLForConditionalGeneration.from_pretrained(
            self.small_model_checkpoint, device_map=torch_device, torch_dtype=torch.bfloat16
        )
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "url": "http://images.cocodataset.org/val2017/000000039769.jpg"},
                    {"type": "text", "text": "Please describe the image explicitly."},
                ],
            }
        ]

        inputs = processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True, return_dict=True, return_tensors="pt"
        ).to(torch_device)
        with torch.no_grad():
            generate_ids = model.generate(**inputs, max_new_tokens=20, do_sample=False)
            decoded_output = processor.decode(
                generate_ids[0, inputs["input_ids"].shape[1] :], skip_special_tokens=True
            )
        expected_output = "The image shows two cats lying on a pink couch. One cat is on the left side of the"
        self.assertEqual(decoded_output, expected_output)

    @slow
    @require_torch_gpu
    def test_qwen2_small_model_integration_batched_generate(self):
        processor = AutoProcessor.from_pretrained(self.small_model_checkpoint)
        model = InternVLForConditionalGeneration.from_pretrained(
            self.small_model_checkpoint, device_map=torch_device, torch_dtype=torch.bfloat16
        )
        # Prepare inputs
        prompt = [
            "<|im_start|>user\n<image>\nWrite a haiku for this image<|im_end|>\n<|im_start|>assistant\n",
            "<|im_start|>user\n<image>\nDescribe this image<|im_end|>\n<|im_start|>assistant\n",
        ]
        image1 = Image.open(requests.get("https://llava-vl.github.io/static/images/view.jpg", stream=True).raw)
        image2 = Image.open(requests.get("https://www.ilankelman.org/stopsigns/australia.jpg", stream=True).raw)

        inputs = processor(text=prompt, images=[[image1], [image2]], padding=True, return_tensors="pt").to(
            torch_device
        )

        output = model.generate(**inputs, do_sample=False, max_new_tokens=25)

        # Check first output
        decoded_output = processor.decode(output[0], skip_special_tokens=True)
        expected_output = "user\n\nWrite a haiku for this image\nassistant\nA pier stretches to the horizon,\nIn misty mountains, nature's still,\nPeace awaits beneath the sky."  # fmt: skip
        self.assertEqual(
            decoded_output,
            expected_output,
            f"Decoded output: {decoded_output}\nExpected output: {expected_output}",
        )

        # Check second output
        decoded_output = processor.decode(output[1], skip_special_tokens=True)
        expected_output = 'user\n\nDescribe this image\nassistant\nThe image shows a street scene with a traditional Chinese gate in the background, featuring red pillars and ornate decorations. There is'  # fmt: skip

        self.assertEqual(
            decoded_output,
            expected_output,
            f"Decoded output: {decoded_output}\nExpected output: {expected_output}",
        )

    @slow
    @require_torch_gpu
    def test_qwen2_small_model_integration_batched_generate_multi_image(self):
        processor = AutoProcessor.from_pretrained(self.small_model_checkpoint)
        model = InternVLForConditionalGeneration.from_pretrained(
            self.small_model_checkpoint, device_map=torch_device, torch_dtype=torch.bfloat16
        )
        # Prepare inputs
        prompt = [
            "<|im_start|>user\n<image>\nWrite a haiku for this image<|im_end|>\n<|im_start|>assistant\n",
            "<|im_start|>user\n<image><image>\nWhat are the differences between these two images?<|im_end|>\n<|im_start|>assistant\n",
        ]
        image1 = Image.open(requests.get("https://llava-vl.github.io/static/images/view.jpg", stream=True).raw)
        image2 = Image.open(
            BytesIO(
                requests.get(
                    "https://cdn.britannica.com/61/93061-050-99147DCE/Statue-of-Liberty-Island-New-York-Bay.jpg"
                ).content
            )
        )
        image3 = Image.open(
            BytesIO(
                requests.get(
                    "https://thumbs.dreamstime.com/b/golden-gate-bridge-san-francisco-purple-flowers-california-echium-candicans-36805947.jpg"
                ).content
            )
        )

        inputs = processor(text=prompt, images=[[image1], [image2, image3]], padding=True, return_tensors="pt").to(
            torch_device
        )

        output = model.generate(**inputs, do_sample=False, max_new_tokens=25)

        # Check first output
        decoded_output = processor.decode(output[0], skip_special_tokens=True)
        # Batching seems to alter the output slightly, but it is also the case in the original implementation. This seems to be expected: https://github.com/huggingface/transformers/issues/23017#issuecomment-1649630232
        expected_output = 'user\n\nWrite a haiku for this image\nassistant\nA pier stretches to the horizon,\nWhere mountains meet a tranquil lake,\nDreams of the unknown begin.'  # fmt: skip
        self.assertEqual(
            decoded_output,
            expected_output,
            f"Decoded output: {decoded_output}\nExpected output: {expected_output}",
        )

        # Check second output
        decoded_output = processor.decode(output[1], skip_special_tokens=True)
        expected_output = 'user\n\nWhat are the differences between these two images?\nassistant\nThe image on the right appears to be a different scene from the one on the left. Here are some differences:\n\n1.'  # fmt: skip
        self.assertEqual(
            decoded_output,
            expected_output,
            f"Decoded output: {decoded_output}\nExpected output: {expected_output}",
        )

    @slow
    @require_torch_gpu
    @require_bitsandbytes
    def test_qwen2_medium_model_integration_video(self):
        processor = AutoProcessor.from_pretrained(self.medium_model_checkpoint)
        quantization_config = BitsAndBytesConfig(load_in_4bit=True)
        model = InternVLForConditionalGeneration.from_pretrained(
            self.medium_model_checkpoint, quantization_config=quantization_config
        )
        # Prepare inputs
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "video",
                        "url": "https://huggingface.co/datasets/hf-internal-testing/fixtures_videos/resolve/main/tennis.mp4",
                    },
                    {"type": "text", "text": "What type of shot is the man performing?"},
                ],
            }
        ]
        inputs = processor.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
            sample_indices_fn=partial(processor.sample_indices_fn, num_frames=8, initial_shift=True),
        ).to(torch_device)

        output = model.generate(**inputs, do_sample=False, max_new_tokens=25)

        decoded_output = processor.decode(output[0, inputs["input_ids"].shape[1] :], skip_special_tokens=True)
        expected_output = 'The man is performing a forehand stroke. This is evident from his stance and the position of his body relative to the net'  # fmt: skip
        self.assertEqual(
            decoded_output,
            expected_output,
            f"Decoded output: {decoded_output}\nExpected output: {expected_output}",
        )

    @slow
    @require_torch_gpu
    @require_bitsandbytes
    def test_qwen2_small_model_integration_interleaved_images_videos(self):
        processor = AutoProcessor.from_pretrained(self.small_model_checkpoint)
        model = InternVLForConditionalGeneration.from_pretrained(
            self.small_model_checkpoint, torch_dtype=torch.bfloat16, device_map=torch_device
        )
        messages = [
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "url": "https://cdn.britannica.com/61/93061-050-99147DCE/Statue-of-Liberty-Island-New-York-Bay.jpg",
                        },
                        {
                            "type": "image",
                            "url": "https://thumbs.dreamstime.com/b/golden-gate-bridge-san-francisco-purple-flowers-california-echium-candicans-36805947.jpg",
                        },
                        {"type": "text", "text": "What are the differences between these two images?"},
                    ],
                },
            ],
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "video",
                            "url": "https://huggingface.co/datasets/hf-internal-testing/fixtures_videos/resolve/main/tennis.mp4",
                        },
                        {"type": "text", "text": "What type of shot is the man performing?"},
                    ],
                },
            ],
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "url": "https://llava-vl.github.io/static/images/view.jpg",
                        },
                        {"type": "text", "text": "Write a haiku for this image"},
                    ],
                }
            ],
        ]
        inputs = processor.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
            padding=True,
            sample_indices_fn=partial(processor.sample_indices_fn, num_frames=8, initial_shift=True),
        ).to(torch_device)

        output = model.generate(**inputs, do_sample=False, max_new_tokens=25)

        decoded_output = processor.decode(output[0], skip_special_tokens=True)
        # Batching seems to alter the output slightly, but it is also the case in the original implementation. This seems to be expected: https://github.com/huggingface/transformers/issues/23017#issuecomment-1649630232
        expected_output = "user\n\n\nWhat are the differences between these two images?\nassistant\n1. The Statue of Liberty is in the foreground, while the Golden Gate Bridge is in the background.\n2. The sky"  # fmt: skip
        self.assertEqual(
            decoded_output,
            expected_output,
            f"Decoded output: {decoded_output}\nExpected output: {expected_output}",
        )

        # Check second output
        decoded_output = processor.decode(output[1], skip_special_tokens=True)
        expected_output = "user\nFrame1: \nFrame2: \nFrame3: \nFrame4: \nFrame5: \nFrame6: \nFrame7: \nFrame8: \nWhat type of shot is the man performing?\nassistant\nThe man is performing a forehand shot."  # fmt: skip
        self.assertEqual(
            decoded_output,
            expected_output,
            f"Decoded output: {decoded_output}\nExpected output: {expected_output}",
        )

        # Check third output
        decoded_output = processor.decode(output[2], skip_special_tokens=True)
        expected_output = "user\n\nWrite a haiku for this image\nassistant\nA pier stretches to the horizon,\nIn the stillness of the lake, a quiet dream,\nNature's peace unfolds."  # fmt: skip
        self.assertEqual(
            decoded_output,
            expected_output,
            f"Decoded output: {decoded_output}\nExpected output: {expected_output}",
        )


@require_torch
class InternVLLlamaIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.small_model_checkpoint = "../InternVLTest-2B"
        self.medium_model_checkpoint = "../InternVLTest-8B"

    def tearDown(self):
        cleanup(torch_device, gc_collect=True)

    @slow
    @require_torch_gpu
    def test_llama_small_model_integration_generate(self):
        processor = AutoProcessor.from_pretrained(self.small_model_checkpoint)
        model = InternVLForConditionalGeneration.from_pretrained(
            self.small_model_checkpoint, device_map=torch_device, torch_dtype=torch.bfloat16
        )
        url = "http://images.cocodataset.org/val2017/000000039769.jpg"
        image = Image.open(requests.get(url, stream=True).raw)

        prompt = "<|im_start|>user\n<image>\nPlease describe the image explicitly.<|im_end|>\n<|im_start|>assistant\n"
        inputs = processor(images=image, text=prompt, return_tensors="pt").to(torch_device)
        with torch.no_grad():
            generate_ids = model.generate(**inputs, max_new_tokens=20, do_sample=False)
            decoded_output = processor.decode(
                generate_ids[0, inputs["input_ids"].shape[1] :], skip_special_tokens=True
            )
        expected_output = "The image shows two cats sleeping on a pink couch. They are lying side by side, with their"
        self.assertEqual(decoded_output, expected_output)

    @slow
    @require_torch_gpu
    def test_llama_small_model_integration_forward(self):
        processor = AutoProcessor.from_pretrained(self.small_model_checkpoint)
        model = InternVLForConditionalGeneration.from_pretrained(
            self.small_model_checkpoint, device_map=torch_device, torch_dtype=torch.bfloat16
        )
        url = "http://images.cocodataset.org/val2017/000000039769.jpg"
        image = Image.open(requests.get(url, stream=True).raw)

        prompt = "<|im_start|>user\n<image>\nPlease describe the image explicitly.<|im_end|>\n<|im_start|>assistant\n"
        inputs = processor(images=image, text=prompt, return_tensors="pt").to(torch_device)

        # Forward
        with torch.inference_mode():
            output = model(**inputs)

        actual_logits = output.logits[0, -1, :5].cpu()
        expected_logits = torch.tensor([-9.8750, -0.4258, 1.4844, -10.3125, -10.3125], dtype=torch.bfloat16)
        # The original implementation and the transformers implementation do not match exactly, hence the higher tolerance.
        # The difference is likely due to the different implementations of the attention mechanism (different order of operations)
        # between the transformers Llama model and the original InternLM model.
        # The difference has almost no effect on the output tokens, but it does affect the logits a lot more.
        self.assertTrue(
            torch.allclose(actual_logits, expected_logits, atol=1),
            f"Actual logits: {actual_logits}"
            f"\nExpected logits: {expected_logits}"
            f"\nDifference: {torch.abs(actual_logits - expected_logits)}",
        )

    @slow
    @require_torch_gpu
    def test_llama_small_model_integration_generate_text_only(self):
        processor = AutoProcessor.from_pretrained(self.small_model_checkpoint)
        model = InternVLForConditionalGeneration.from_pretrained(
            self.small_model_checkpoint, device_map=torch_device, torch_dtype=torch.bfloat16
        )
        prompt = "<|im_start|>user\nWrite a haiku<|im_end|>\n<|im_start|>assistant\n"
        inputs = processor(text=prompt, return_tensors="pt").to(torch_device)
        with torch.no_grad():
            generate_ids = model.generate(**inputs, max_new_tokens=200, do_sample=False)
            decoded_output = processor.decode(
                generate_ids[0, inputs["input_ids"].shape[1] :], skip_special_tokens=True
            )
        expected_output = "Autumn leaves fall,\nNature's breath, a season's sigh,\nSilent woods awake."
        self.assertEqual(decoded_output, expected_output)

    @slow
    @require_torch_gpu
    def test_llama_small_model_integration_generate_chat_template(self):
        processor = AutoProcessor.from_pretrained(self.small_model_checkpoint)
        model = InternVLForConditionalGeneration.from_pretrained(
            self.small_model_checkpoint, device_map=torch_device, torch_dtype=torch.bfloat16
        )
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "url": "http://images.cocodataset.org/val2017/000000039769.jpg"},
                    {"type": "text", "text": "Please describe the image explicitly."},
                ],
            }
        ]

        inputs = processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True, return_dict=True, return_tensors="pt"
        ).to(torch_device)
        with torch.no_grad():
            generate_ids = model.generate(**inputs, max_new_tokens=20, do_sample=False)
            decoded_output = processor.decode(
                generate_ids[0, inputs["input_ids"].shape[1] :], skip_special_tokens=True
            )
        expected_output = "The image shows two cats sleeping on a pink couch. They are lying side by side, with their"
        self.assertEqual(decoded_output, expected_output)

    @slow
    @require_torch_gpu
    def test_llama_small_model_integration_batched_generate(self):
        processor = AutoProcessor.from_pretrained(self.small_model_checkpoint)
        model = InternVLForConditionalGeneration.from_pretrained(
            self.small_model_checkpoint, device_map=torch_device, torch_dtype=torch.bfloat16
        )
        # Prepare inputs
        prompt = [
            "<|im_start|>user\n<image>\nWrite a haiku for this image<|im_end|>\n<|im_start|>assistant\n",
            "<|im_start|>user\n<image>\nDescribe this image<|im_end|>\n<|im_start|>assistant\n",
        ]
        image1 = Image.open(requests.get("https://llava-vl.github.io/static/images/view.jpg", stream=True).raw)
        image2 = Image.open(requests.get("https://www.ilankelman.org/stopsigns/australia.jpg", stream=True).raw)

        inputs = processor(text=prompt, images=[[image1], [image2]], padding=True, return_tensors="pt").to(
            torch_device
        )

        output = model.generate(**inputs, do_sample=False, max_new_tokens=25)

        # Check first output
        decoded_output = processor.decode(output[0], skip_special_tokens=True)
        expected_output = 'user\n\nWrite a haiku for this image\nassistant\nMajestic snow-capped peaks,\nWooden dock stretches to the sea,\nSilent water mirrors.'  # fmt: skip
        self.assertEqual(
            decoded_output,
            expected_output,
            f"Decoded output: {decoded_output}\nExpected output: {expected_output}",
        )

        # Check second output
        decoded_output = processor.decode(output[1], skip_special_tokens=True)
        expected_output = 'user\n\nDescribe this image\nassistant\nThe image shows a street scene with a traditional Chinese gate in the background, adorned with red and gold colors and Chinese characters'  # fmt: skip

        self.assertEqual(
            decoded_output,
            expected_output,
            f"Decoded output: {decoded_output}\nExpected output: {expected_output}",
        )

    @slow
    @require_torch_gpu
    def test_llama_small_model_integration_batched_generate_multi_image(self):
        processor = AutoProcessor.from_pretrained(self.small_model_checkpoint)
        model = InternVLForConditionalGeneration.from_pretrained(
            self.small_model_checkpoint, device_map=torch_device, torch_dtype=torch.bfloat16
        )
        # Prepare inputs
        prompt = [
            "<|im_start|>user\n<image>\nWrite a haiku for this image<|im_end|>\n<|im_start|>assistant\n",
            "<|im_start|>user\n<image><image>\nWhat are the difference between these two images?<|im_end|>\n<|im_start|>assistant\n",
        ]
        image1 = Image.open(requests.get("https://llava-vl.github.io/static/images/view.jpg", stream=True).raw)
        image2 = Image.open(
            BytesIO(
                requests.get(
                    "https://cdn.britannica.com/61/93061-050-99147DCE/Statue-of-Liberty-Island-New-York-Bay.jpg"
                ).content
            )
        )
        image3 = Image.open(
            BytesIO(
                requests.get(
                    "https://thumbs.dreamstime.com/b/golden-gate-bridge-san-francisco-purple-flowers-california-echium-candicans-36805947.jpg"
                ).content
            )
        )

        inputs = processor(text=prompt, images=[[image1], [image2, image3]], padding=True, return_tensors="pt").to(
            torch_device
        )

        output = model.generate(**inputs, do_sample=False, max_new_tokens=25)

        # Check first output
        decoded_output = processor.decode(output[0], skip_special_tokens=True)
        # Batching seems to alter the output slightly, but it is also the case in the original implementation. This seems to be expected: https://github.com/huggingface/transformers/issues/23017#issuecomment-1649630232
        expected_output = 'user\n\nWrite a haiku for this image\nassistant\nMajestic snow-capped peaks,\nWooden dock stretches to the sea,\nSilent water mirrors.'  # fmt: skip
        self.assertEqual(
            decoded_output,
            expected_output,
            f"Decoded output: {decoded_output}\nExpected output: {expected_output}",
        )

        # Check second output
        decoded_output = processor.decode(output[1], skip_special_tokens=True)
        expected_output = 'user\n\nWhat are the difference between these two images?\nassistant\nI apologize for the confusion in my previous response. Upon closer inspection, the differences between the two images are quite subtle and likely'  # fmt: skip
        self.assertEqual(
            decoded_output,
            expected_output,
            f"Decoded output: {decoded_output}\nExpected output: {expected_output}",
        )

    @slow
    @require_torch_gpu
    @require_bitsandbytes
    def test_llama_medium_model_integration_video(self):
        processor = AutoProcessor.from_pretrained(self.medium_model_checkpoint)
        quantization_config = BitsAndBytesConfig(load_in_4bit=True)
        model = InternVLForConditionalGeneration.from_pretrained(
            self.medium_model_checkpoint, quantization_config=quantization_config
        )
        # Prepare inputs
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "video",
                        "url": "https://huggingface.co/datasets/hf-internal-testing/fixtures_videos/resolve/main/tennis.mp4",
                    },
                    {"type": "text", "text": "What type of shot is the man performing?"},
                ],
            }
        ]
        inputs = processor.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
            sample_indices_fn=partial(processor.sample_indices_fn, num_frames=8, initial_shift=True),
        ).to(torch_device)

        output = model.generate(**inputs, do_sample=False, max_new_tokens=25)

        decoded_output = processor.decode(output[0, inputs["input_ids"].shape[1] :], skip_special_tokens=True)
        expected_output = "The man is performing a forehand shot."
        self.assertEqual(
            decoded_output,
            expected_output,
            f"Decoded output: {decoded_output}\nExpected output: {expected_output}",
        )

    @slow
    @require_torch_gpu
    @require_bitsandbytes
    def test_llama_small_model_integration_interleaved_images_videos(self):
        processor = AutoProcessor.from_pretrained(self.small_model_checkpoint)
        model = InternVLForConditionalGeneration.from_pretrained(
            self.small_model_checkpoint, torch_dtype=torch.bfloat16, device_map=torch_device
        )
        messages = [
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "url": "https://cdn.britannica.com/61/93061-050-99147DCE/Statue-of-Liberty-Island-New-York-Bay.jpg",
                        },
                        {
                            "type": "image",
                            "url": "https://thumbs.dreamstime.com/b/golden-gate-bridge-san-francisco-purple-flowers-california-echium-candicans-36805947.jpg",
                        },
                        {"type": "text", "text": "What are the difference between these two images?"},
                    ],
                },
            ],
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "video",
                            "url": "https://huggingface.co/datasets/hf-internal-testing/fixtures_videos/resolve/main/tennis.mp4",
                        },
                        {"type": "text", "text": "What type of shot is the man performing?"},
                    ],
                },
            ],
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "url": "https://llava-vl.github.io/static/images/view.jpg",
                        },
                        {"type": "text", "text": "Write a haiku for this image"},
                    ],
                }
            ],
        ]
        inputs = processor.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
            padding=True,
            sample_indices_fn=partial(processor.sample_indices_fn, num_frames=8, initial_shift=True),
        ).to(torch_device)

        output = model.generate(**inputs, do_sample=False, max_new_tokens=25)

        decoded_output = processor.decode(output[0], skip_special_tokens=True)
        # Batching seems to alter the output slightly, but it is also the case in the original implementation. This seems to be expected: https://github.com/huggingface/transformers/issues/23017#issuecomment-1649630232
        expected_output = 'user\n\n\nWhat are the difference between these two images?\nassistant\nI apologize for the confusion in my previous response. Upon closer inspection, the differences between the two images are:\n\n1. **'  # fmt: skip
        self.assertEqual(
            decoded_output,
            expected_output,
            f"Decoded output: {decoded_output}\nExpected output: {expected_output}",
        )

        # Check second output
        decoded_output = processor.decode(output[1], skip_special_tokens=True)
        expected_output = 'user\nFrame1: \nFrame2: \nFrame3: \nFrame4: \nFrame5: \nFrame6: \nFrame7: \nFrame8: \nWhat type of shot is the man performing?\nassistant\nThe man is performing a forehand shot. This is a common shot in tennis where the player swings the racket across their'  # fmt: skip
        self.assertEqual(
            decoded_output,
            expected_output,
            f"Decoded output: {decoded_output}\nExpected output: {expected_output}",
        )

        # Check third output
        decoded_output = processor.decode(output[2], skip_special_tokens=True)
        expected_output = 'user\n\nWrite a haiku for this image\nassistant\nMajestic snow-capped peaks,\nWooden dock stretches to the sea,\nSilent water mirrors.'  # fmt: skip
        self.assertEqual(
            decoded_output,
            expected_output,
            f"Decoded output: {decoded_output}\nExpected output: {expected_output}",
        )
