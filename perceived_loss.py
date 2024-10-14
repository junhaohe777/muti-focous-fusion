import torch
import torch.nn as nn
from torchvision.models import vgg19

class PerceptualLoss(nn.Module):
    def __init__(self):
        super(PerceptualLoss, self).__init__()

        self.vgg = vgg19(pretrained=True).features[:20].eval() 
        for param in self.vgg.parameters():
            param.requires_grad = False  
        self.vgg = self.vgg.cuda()
    def forward(self, predicted, real):
        
        predicted_features = self.vgg(predicted.cuda())
        real_features = self.vgg(real.cuda())
        
        loss = torch.mean((predicted_features - real_features) ** 2)
        
        return loss