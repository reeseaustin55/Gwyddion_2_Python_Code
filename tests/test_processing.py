from gwyddion_pipeline import data_processing
from gwyddion_pipeline.pipeline import GwyddionProcessor, ProcessingOptions


def _create_gradient(size: int) -> list[list[float]]:
    return [[(row + col) / (2 * size) for col in range(size)] for row in range(size)]


def test_pipeline_scars_ordering():
    base = _create_gradient(8)
    noisy = [row[:] for row in base]
    noisy[2][3] = 5.0
    noisy[5][1] = -4.0

    options = ProcessingOptions(remove_scars=True, scale_factor=2.0)
    processor = GwyddionProcessor(options)
    result = processor.process_data(noisy)

    manual = data_processing.align_rows(noisy)
    manual = data_processing.remove_scars(manual)
    manual = data_processing.scale_data(manual, 2.0)
    manual = data_processing.remove_scars(manual)
    manual = data_processing.align_rows(manual)

    assert result.data == manual


def test_psdf_and_angular_spectrum():
    grid = [[(row + col) % 2 for col in range(6)] for row in range(6)]
    options = ProcessingOptions(generate_psdf=True, generate_angular_spectrum=True)
    processor = GwyddionProcessor(options)
    result = processor.process_data(grid)

    assert result.psdf is not None
    assert result.angular_spectrum is not None
    assert len(result.angular_spectrum.intensity) == 180


def test_frame_stabilisation_with_limit():
    frame = [[0.0 for _ in range(8)] for _ in range(8)]
    for row in range(2, 4):
        for col in range(2, 4):
            frame[row][col] = 1.0
    shifted = [[0.0 for _ in range(8)] for _ in range(8)]
    for row in range(2, 4):
        for col in range(4, 6):
            shifted[row][col] = 1.0

    options = ProcessingOptions(enable_stabilisation=True, max_frame_displacement_pct=50.0)
    processor = GwyddionProcessor(options)
    stabilised = processor.stabilise_frames([frame, shifted])

    assert len(stabilised) == 2
    assert stabilised[1][2][2] == 1.0

