from PIL import Image
import torch
import fire

from processing_paligemma import PaliGemmaProcessor
from modeling_gemma import KVCache, PaliGemmaForConditionalGeneration
from utils import load_hf_model


def move_inputs_to_device(model_inputs: dict, device: str):
    # Moves the inputs (tensors) to the specified device (e.g., CPU, GPU).
    model_inputs = {k: v.to(device) for k, v in model_inputs.items()}
    return model_inputs


def get_model_inputs(
    processor: PaliGemmaProcessor, prompt: str, image_file_path: str, device: str
):
    # Prepares model inputs by processing the text prompt and image.
    # Uses the processor to tokenize the text and preprocess the image,
    # then moves the inputs to the specified device.
    image = Image.open(image_file_path)
    images = [image]
    prompts = [prompt]
    model_inputs = processor(text=prompts, images=images)
    model_inputs = move_inputs_to_device(model_inputs, device)
    return model_inputs


def test_inference(
    model: PaliGemmaForConditionalGeneration,
    processor: PaliGemmaProcessor,
    device: str,
    prompt: str,
    image_file_path: str,
    max_tokens_to_generate: int,
    temperature: float,
    top_p: float,
    do_sample: bool,
):
    # Performs inference on the given model using the specified text prompt and image.
    # Generates a sequence of tokens by iteratively sampling or taking the most likely token.
    model_inputs = get_model_inputs(processor, prompt, image_file_path, device)
    input_ids = model_inputs["input_ids"]
    attention_mask = model_inputs["attention_mask"]
    pixel_values = model_inputs["pixel_values"]

    kv_cache = KVCache()  # Key-Value cache to store intermediate attention states.

    stop_token = processor.tokenizer.eos_token_id  # End-of-sequence token ID.
    generated_tokens = []

    for _ in range(max_tokens_to_generate):
        # Perform a forward pass to generate the next token logits.
        outputs = model(
            input_ids=input_ids,
            pixel_values=pixel_values,
            attention_mask=attention_mask,
            kv_cache=kv_cache,
        )
        kv_cache = outputs["kv_cache"]  # Update the key-value cache.
        next_token_logits = outputs["logits"][:, -1, :]  # Get logits for the last token.

        # Sample the next token based on the logits.
        if do_sample:
            next_token_logits = torch.softmax(next_token_logits / temperature, dim=-1)
            next_token = _sample_top_p(next_token_logits, top_p)
        else:
            next_token = torch.argmax(next_token_logits, dim=-1, keepdim=True)

        assert next_token.size() == (1, 1)
        next_token = next_token.squeeze(0)  # Remove batch dimension.
        generated_tokens.append(next_token)

        # Stop if the end-of-sequence token is generated.
        if next_token.item() == stop_token:
            break

        # Append the next token to the input for the next iteration.
        input_ids = next_token.unsqueeze(-1)
        attention_mask = torch.cat(
            [attention_mask, torch.ones((1, 1), device=input_ids.device)], dim=-1
        )

    generated_tokens = torch.cat(generated_tokens, dim=-1)
    decoded = processor.tokenizer.decode(generated_tokens, skip_special_tokens=True)
    print(prompt + decoded)


def _sample_top_p(probs: torch.Tensor, p: float):
    # Implements nucleus sampling (Top-P sampling) to sample a token from the probability distribution.
    # Filters out low-probability tokens and redistributes remaining probabilities.
    probs_sort, probs_idx = torch.sort(probs, dim=-1, descending=True)
    probs_sum = torch.cumsum(probs_sort, dim=-1)
    mask = probs_sum - probs_sort > p  # Mask for tokens exceeding the cumulative probability p.
    probs_sort[mask] = 0.0  # Set probabilities of masked tokens to 0.
    probs_sort.div_(probs_sort.sum(dim=-1, keepdim=True))  # Normalize remaining probabilities.
    next_token = torch.multinomial(probs_sort, num_samples=1)  # Sample a token.
    next_token = torch.gather(probs_idx, -1, next_token)  # Map to the original token indices.
    return next_token


def main(
    model_path: str = None,
    prompt: str = None,
    image_file_path: str = None,
    max_tokens_to_generate: int = 100,
    temperature: float = 0.8,
    top_p: float = 0.9,
    do_sample: bool = False,
    only_cpu: bool = False,
):
    # Main function to load the model, prepare inputs, and run inference.
    # Supports running on CPU, CUDA, or MPS (if available).
    device = "cpu"

    if not only_cpu:
        if torch.cuda.is_available():
            device = "cuda"
        elif torch.backends.mps.is_available():
            device = "mps"

    print("Device in use: ", device)

    print(f"Loading model")
    model, tokenizer = load_hf_model(model_path, device)  # Load model and tokenizer.
    model = model.to(device).eval()  # Move the model to the specified device and set to evaluation mode.

    num_image_tokens = model.config.vision_config.num_image_tokens  # Number of image tokens in the model.
    image_size = model.config.vision_config.image_size  # Input image size expected by the model.
    processor = PaliGemmaProcessor(tokenizer, num_image_tokens, image_size)

    print("Running inference")
    with torch.no_grad():
        test_inference(
            model,
            processor,
            device,
            prompt,
            image_file_path,
            max_tokens_to_generate,
            temperature,
            top_p,
            do_sample,
        )


if __name__ == "__main__":
    fire.Fire(main)  # Use the Fire library to create a CLI for the script.