'''
Pytorch implementation of ResNet models.
Reference:
[1] He, K., Zhang, X., Ren, S., Sun, J.: Deep residual learning for image recognition. In: CVPR, 2016.
'''
import torch
import math
import torch.nn as nn
import torch.nn.functional as F
from utils import gumbel_like
from utils import gumbel_softmax_v1 as gumbel_softmax
from typing import Union, List

# --- HELPERS ---

def conv3x3(in_planes, out_planes, stride=1):
    '''
        3x3 convolution with padding
    '''
    return nn.Conv2d(in_planes, out_planes, kernel_size=3, stride=stride, padding=1, bias=False)


# --- COMPONENTS ---

class BasicBlock(nn.Module):

    expansion = 1

    def __init__(self, inplanes, planes, stride=1, downsample=None):
        super(BasicBlock, self).__init__()
        self.conv1 = conv3x3(inplanes, planes, stride)
        self.bn1 = nn.BatchNorm2d(planes)
        self.relu = nn.ReLU(inplace=True)
        self.conv2 = conv3x3(planes, planes)
        self.bn2 = nn.BatchNorm2d(planes)
        self.downsample = downsample
        self.stride = stride

    def forward(self, x):
        residual = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.conv2(out)
        out = self.bn2(out)

        if self.downsample is not None:
            residual = self.downsample(x)

        out += residual
        out = self.relu(out)

        return out



class Bottleneck(nn.Module):

    expansion = 4

    def __init__(self, inplanes, planes, stride=1, downsample=None):
        super(Bottleneck, self).__init__()
        self.conv1 = nn.Conv2d(inplanes, planes, kernel_size=1, bias=False)
        self.bn1 = nn.BatchNorm2d(planes)
        self.conv2 = nn.Conv2d(planes, planes, kernel_size=3, stride=stride, padding=1, bias=False)
        self.bn2 = nn.BatchNorm2d(planes)
        self.conv3 = nn.Conv2d(planes, planes * 4, kernel_size=1, bias=False)
        self.bn3 = nn.BatchNorm2d(planes * 4)
        self.relu = nn.ReLU(inplace=True)
        self.downsample = downsample
        self.stride = stride

    def forward(self, x):
        residual = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.conv2(out)
        out = self.bn2(out)
        out = self.relu(out)

        out = self.conv3(out)
        out = self.bn3(out)

        if self.downsample is not None:
            residual = self.downsample(x)

        out += residual
        out = self.relu(out)

        return out


class ResNet(nn.Module):

    def __init__(self, block, layers, criterion=None, tau=0.1, num_classes=200, temp=1.0, weight_root=None):

        self.inplanes = 64
        super(ResNet, self).__init__()
        self.conv1 = nn.Conv2d(3, 64, kernel_size=3, stride=1, padding=1,  bias=False)
        self.bn1 = nn.BatchNorm2d(64)
        self.relu = nn.ReLU(inplace=True)
        #self.maxpool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)
        self.layer1 = self._make_layer(block, 64, layers[0])
        self.layer2 = self._make_layer(block, 128, layers[1], stride=2)
        self.layer3 = self._make_layer(block, 256, layers[2], stride=2)
        self.layer4 = self._make_layer(block, 512, layers[3], stride=2)
        self.avgpool = nn.AdaptiveAvgPool2d(2)
        self.fc = nn.Linear(512 * block.expansion * 4, num_classes)
        self.temp = temp
        self.block_names = ['layer1', 'layer2', 'layer3', 'layer4', 'fc']
        self.weight_root = weight_root
        self._block = block
        self._num_classes = num_classes
        self._criterion = criterion
        self._tau = tau
        self.model_name = "resnet50_ti"


        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
                m.weight.data.normal_(0, math.sqrt(2. / n))
            elif isinstance(m, nn.BatchNorm2d):
                m.weight.data.fill_(1)
                m.bias.data.zero_()

        self._initialize_alphas()

    def load_gumbel_weight(self):
        weights = self.arch_weights(cat=False)
        index = torch.argmax(weights, -1)

        saved_model_name = "{}/{}_cross_entropy_{}.model".format(self.weight_root, self.model_name, 42)
        model_dict = torch.load(str(saved_model_name))
        self.load_state_dict(model_dict, strict=False)

        for block_name in self.block_names:
            idx = self.block_names.index(block_name)
            saved_model_name = "{}/{}_cross_entropy_{}.model".format(self.weight_root, self.model_name, index[idx] + 1)
            model_dict = torch.load(str(saved_model_name))
            if block_name == 'fc':
                modified_dict = {k[3:]: v for k, v in model_dict.items() if k.startswith(block_name)}
                model_dict.update(modified_dict)
            else:
                modified_dict = {k[7:]: v for k, v in model_dict.items() if k.startswith(block_name)}
                model_dict.update(modified_dict)
            getattr(self, block_name).load_state_dict(model_dict, strict=False)


        return index

    def _initialize_alphas(self):
        # number of layers
        k = 5
        # number of candidates
        num_ops = 100

        # init architecture parameters alpha
        self.alphas = (1e-3 * torch.randn(k, num_ops)).to('cuda').requires_grad_(True)
        # init Gumbel distribution for Gumbel softmax sampler
        self.gumbel = gumbel_like(self.alphas)

    def arch_parameters(self) -> List[torch.tensor]:
        return [self.alphas]

    def arch_weights(self, smooth=False, cat: bool=True) -> Union[List[torch.tensor], torch.tensor]:
        if smooth:
            # TODO
            pass
        weights = gumbel_softmax(self.alphas, tau=self._tau, dim=-1, g=self.gumbel)
        if cat:
            return torch.cat(weights)
        else:
            return weights

    def _make_layer(self, block, planes, blocks, stride=1):
        downsample = None
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                nn.Conv2d(self.inplanes, planes * block.expansion, kernel_size=1, stride=stride, bias=False),
                nn.BatchNorm2d(planes * block.expansion),
            )

        layers = []
        layers.append(block(self.inplanes, planes, stride, downsample))
        self.inplanes = planes * block.expansion
        for i in range(1, blocks):
            layers.append(block(self.inplanes, planes))

        return nn.Sequential(*layers)

    def forward(self, x):
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        #x = self.maxpool(x)

        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)

        x = self.avgpool(x)
        x = x.view(x.size(0), -1)
        x = self.fc(x) / self.temp

        return x

    def load_combination_weight(self, combination, weight_folder, model_name):

        saved_model_name = "{}/{}_cross_entropy_{}.model".format(weight_folder, model_name, 50)
        model_dict = torch.load(str(saved_model_name))
        # modified_dict = {k[7:]: v for k, v in model_dict.items()}
        # model_dict.update(modified_dict)
        self.load_state_dict(model_dict, strict=True)

        for block_name in self.block_names:
            idx = self.block_names.index(block_name)
            saved_model_name = "{}/{}_cross_entropy_{}.model".format(weight_folder, model_name,
                combination[idx] + 1)
            model_dict = torch.load(str(saved_model_name))
            if block_name == 'fc':
                modified_dict = {k[3:]: v for k, v in model_dict.items() if k.startswith(block_name)}
                model_dict.update(modified_dict)
            else:
                modified_dict = {k[7:]: v for k, v in model_dict.items() if k.startswith(block_name)}
                model_dict.update(modified_dict)
            getattr(self, block_name).load_state_dict(model_dict, strict=False)

def resnet18(temp=1.0, **kwargs):
    model = ResNet(BasicBlock, [2, 2, 2, 2], temp=temp, **kwargs)
    return model


def resnet34(temp=1.0, **kwargs):
    model = ResNet(BasicBlock, [3, 4, 6, 3], temp=temp, **kwargs)
    return model


def resnet50(temp=1.0, **kwargs):
    model = ResNet(Bottleneck, [3, 4, 6, 3], temp=temp, **kwargs)
    return model


def resnet101(temp=1.0, **kwargs):
    model = ResNet(Bottleneck, [3, 4, 23, 3], temp=temp, **kwargs)
    return model


def resnet110(temp=1.0, **kwargs):
    model = ResNet(Bottleneck, [3, 4, 26, 3], temp=temp, **kwargs)
    return model


def resnet152(temp=1.0, **kwargs):
    model = ResNet(Bottleneck, [3, 8, 36, 3], temp=temp, **kwargs)
    return model
