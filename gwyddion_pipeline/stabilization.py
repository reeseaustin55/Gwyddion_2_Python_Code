"""Video stabilisation utilities implemented without external dependencies."""
from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple

from .data_processing import Matrix, _matrix_shape


class FrameStabilizer:
    def stabilise(
        self, frames: Sequence[Sequence[Sequence[float]]], max_displacement_pct: float
    ) -> List[Matrix]:
        frames = [
            [list(map(float, row)) for row in frame]
            for frame in frames
        ]
        if not frames:
            return []

        reference = frames[0]
        rows, cols = _matrix_shape(reference)
        max_shift = max_displacement_pct / 100.0 * cols

        stabilised = [reference]
        cumulative_shift = (0.0, 0.0)

        for frame in frames[1:]:
            shift = self._estimate_shift(reference, frame, max_shift)
            cumulative_shift = (cumulative_shift[0] + shift[0], cumulative_shift[1] + shift[1])
            stabilised.append(self._apply_shift(frame, cumulative_shift))

        return stabilised

    def _estimate_shift(
        self, reference: Matrix, frame: Matrix, max_shift: float
    ) -> Tuple[float, float]:
        rows, cols = _matrix_shape(reference)
        limit = max(1, int(round(max_shift)))
        best_score = float("-inf")
        best_shift = (0.0, 0.0)
        for vertical in range(-limit, limit + 1):
            for horizontal in range(-limit, limit + 1):
                score = self._correlation(reference, frame, vertical, horizontal)
                if score > best_score:
                    best_score = score
                    best_shift = (float(vertical), float(horizontal))
        return best_shift

    def _correlation(
        self, reference: Matrix, frame: Matrix, vertical: int, horizontal: int
    ) -> float:
        rows, cols = _matrix_shape(reference)
        total = 0.0
        for row in range(rows):
            ref_row = reference[row]
            src_row = frame[(row - vertical) % rows]
            for col in range(cols):
                total += ref_row[col] * src_row[(col - horizontal) % cols]
        return total

    def _apply_shift(self, frame: Matrix, shift: Tuple[float, float]) -> Matrix:
        rows, cols = _matrix_shape(frame)
        vertical = int(round(shift[0]))
        horizontal = int(round(shift[1]))
        shifted = [[0.0 for _ in range(cols)] for _ in range(rows)]
        for row in range(rows):
            for col in range(cols):
                new_row = (row + vertical) % rows
                new_col = (col + horizontal) % cols
                shifted[new_row][new_col] = frame[row][col]
        return shifted

    def write_video(self, frames: Iterable[Matrix], path: str) -> None:
        frames = list(frames)
        with open(path, "w", encoding="utf-8") as handle:
            for index, frame in enumerate(frames):
                rows, cols = _matrix_shape(frame)
                handle.write(f"# Frame {index}\n")
                handle.write(f"size {rows} {cols}\n")
                for row in frame:
                    handle.write(" ".join(f"{value:.6f}" for value in row) + "\n")

