from call.app import call as app_call


def test_strip_fetch_images_links_removes_fetch_when_non_fetch_present():
    text = "\n".join(
        [
            "https://media-gen.example.com/media/output_1_media-gen__openai-images-edit_abc.png",
            "https://media-gen.example.com/media/output_2_media-gen__fetch-images_def.jpeg",
            "Model: gpt-image-1",
        ]
    )

    filtered = app_call._strip_fetch_images_links(text)

    assert "fetch-images" not in filtered
    assert "openai-images-edit" in filtered
    assert "Model: gpt-image-1" in filtered


def test_strip_fetch_images_links_keeps_when_only_fetch():
    text = "https://media-gen.example.com/media/output_2_media-gen__fetch-images_def.jpeg"

    filtered = app_call._strip_fetch_images_links(text)

    assert filtered == text


def test_strip_fetch_images_links_handles_anchor_tags():
    text = "\n".join(
        [
            '<a href="https://media-gen.example.com/media/output_3_media-gen__openai-images-edit_ghi.png">result</a>',
            '<a href="https://media-gen.example.com/media/output_4_media-gen__fetch-images_jkl.jpeg">base_image</a>',
        ]
    )

    filtered = app_call._strip_fetch_images_links(text)

    assert "fetch-images" not in filtered
    assert "openai-images-edit" in filtered
