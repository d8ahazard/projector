from app.media.pipeline import display_matrix_rotation


def test_content_rotation_is_inverted_for_mp4_display_matrix():
    assert display_matrix_rotation(0) == 0
    assert display_matrix_rotation(90) == 270
    assert display_matrix_rotation(180) == 180
    assert display_matrix_rotation(270) == 90
