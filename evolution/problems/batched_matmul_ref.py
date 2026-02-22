import torch
import torch.nn as nn


class Model(nn.Module):
    def __init__(self, batch_size: int = 8, M: int = 1024, K: int = 1024, N: int = 1024):
        super().__init__()
        self.batch_size = batch_size
        self.M = M
        self.K = K
        self.N = N

    def forward(self, A: torch.Tensor, B: torch.Tensor) -> torch.Tensor:
        return torch.bmm(A, B)


def get_inputs():
    A = torch.randn(8, 1024, 1024, device='cuda', dtype=torch.float16)
    B = torch.randn(8, 1024, 1024, device='cuda', dtype=torch.float16)
    return [A, B]


def get_init_inputs():
    return [8, 1024, 1024, 1024]
