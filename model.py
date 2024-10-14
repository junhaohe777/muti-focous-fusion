import sys
import torch
import torch.nn as nn

from option import args
from collections import OrderedDict
import torch.nn.functional as F

# ------helper functions------ #

def pad(pad_type, padding):
    pad_type = pad_type.lower()
    if padding == 0:
        return None


def get_valid_padding(kernel_size, dilation):
    kernel_size = kernel_size + (kernel_size - 1) * (dilation - 1)
    padding = (kernel_size - 1) // 2
    return padding


def activation(act_type=args.act_type, slope=0.2, n_prelu=1):
    act_type = act_type.lower()
    if act_type == 'prelu':
        layer = nn.PReLU(num_parameters=n_prelu, init=slope)
    else:
        raise NotImplementedError('[ERROR] Activation layer [%s] is not implemented!' % act_type)
    return layer


def norm(n_feature, norm_type='bn'):
    norm_type = norm_type.lower()
    layer = None
    if norm_type == 'bn':
        layer = nn.BatchNorm2d(n_feature)
    else:
        raise NotImplementedError('[ERROR] Normalization layer [%s] is not implemented!' % norm_type)
    return layer


def sequential(*args):
    if len(args) == 1:
        if isinstance(args[0], OrderedDict):
            raise NotImplementedError('[ERROR] %s.sequential() does not support OrderedDict' % sys.modules[__name__])
        else:
            return args[0]
    modules = []
    for module in args:
        if isinstance(module, nn.Sequential):
            for submodule in module:
                modules.append(submodule)
        elif isinstance(module, nn.Module):
            modules.append(module)
    return nn.Sequential(*modules)


# ------build blocks------ #
def ConvBlock(in_channels, out_channels, kernel_size, stride=1, dilation=1, bias=True, valid_padding=True, padding=0,
              act_type='prelu', norm_type='bn', pad_type='zero'):
    if valid_padding:
        padding = get_valid_padding(kernel_size, dilation)
    else:
        pass
    p = pad(pad_type, padding) if pad_type and pad_type != 'zero' else None
    conv = nn.Conv2d(in_channels, out_channels, kernel_size, stride=stride, padding=padding, dilation=dilation,
                     bias=bias)

    act = activation(act_type) if act_type else None
    n = norm(out_channels, norm_type) if norm_type else None
    return sequential(p, conv, n, act)


def DeconvBlock(in_channels, out_channels, kernel_size, stride=1, dilation=1, bias=True, padding=0, act_type='relu',
                norm_type='bn', pad_type='zero'):
    p = pad(pad_type, padding) if pad_type and pad_type != 'zero' else None
    deconv = nn.ConvTranspose2d(in_channels, out_channels, kernel_size, stride, padding, dilation=dilation, bias=bias)
    act = activation(act_type) if act_type else None
    n = norm(out_channels, norm_type) if norm_type else None
    return sequential(p, deconv, n, act)


# ------build SRB ------ #
class SRB(nn.Module):
    def __init__(self, norm_type):
        super(SRB, self).__init__()
        upscale_factor = args.scale
        if upscale_factor == 2:
            stride = 2
            padding = 2
            kernel_size = 6
        elif upscale_factor == 4:
            stride = 4
            padding = 2
            kernel_size = 8

        self.num_groups = args.num_groups
        num_features = args.num_features
        act_type = args.act_type

        self.compress_in = ConvBlock(num_features, num_features, kernel_size=1, act_type=act_type, norm_type=norm_type)
        self.upBlocks = nn.ModuleList()
        self.downBlocks = nn.ModuleList()
        self.uptranBlocks = nn.ModuleList()
        self.downtranBlocks = nn.ModuleList()

        for idx in range(self.num_groups):
            self.upBlocks.append(
                DeconvBlock(num_features, num_features, kernel_size=kernel_size, stride=stride, padding=padding,
                            act_type=act_type, norm_type=norm_type))
            self.downBlocks.append(
                ConvBlock(num_features, num_features, kernel_size=kernel_size, stride=stride, padding=padding,
                          act_type=act_type, norm_type=norm_type, valid_padding=False))
            if idx > 0:
                self.uptranBlocks.append(
                    ConvBlock(num_features * (idx + 1), num_features, kernel_size=1, stride=1, act_type=act_type,
                              norm_type=norm_type))
                self.downtranBlocks.append(
                    ConvBlock(num_features * (idx + 1), num_features, kernel_size=1, stride=1, act_type=act_type,
                              norm_type=norm_type))

        self.compress_out = ConvBlock(self.num_groups * num_features, num_features, kernel_size=1, act_type=act_type,
                                      norm_type=norm_type)
        self.last_hidden = None

    def forward(self, f_in):
        # use cuda
        f = torch.zeros(f_in.size()).cuda()
        f.copy_(f_in)

        f = self.compress_in(f)

        lr_features = []
        hr_features = []
        lr_features.append(f)

        for idx in range(self.num_groups):
            LD_L = torch.cat(tuple(lr_features), 1)
            if idx > 0:
                LD_L = self.uptranBlocks[idx - 1](LD_L)
            LD_H = self.upBlocks[idx](LD_L)

            hr_features.append(LD_H)

            LD_H = torch.cat(tuple(hr_features), 1)
            if idx > 0:
                LD_H = self.downtranBlocks[idx - 1](LD_H)
            LD_L = self.downBlocks[idx](LD_H)

            lr_features.append(LD_L)

        del hr_features
        g = torch.cat(tuple(lr_features[1:]), 1)
        g = self.compress_out(g)

        return g

# ------build CFB ------ #
class CFB(nn.Module):
    def __init__(self, norm_type):
        super(CFB, self).__init__()
        upscale_factor = args.scale
        if upscale_factor == 2:
            stride = 2
            padding = 2
            kernel_size = 6
        elif upscale_factor == 4:
            stride = 4
            padding = 2
            kernel_size = 8

        self.num_groups = args.num_groups
        num_features = args.num_features
        act_type = args.act_type

        self.compress_in = ConvBlock(3 * num_features, num_features, kernel_size=1, act_type=act_type,
                                     norm_type=norm_type)
        self.upBlocks = nn.ModuleList()
        self.downBlocks = nn.ModuleList()
        self.uptranBlocks = nn.ModuleList()
        self.downtranBlocks = nn.ModuleList()

        self.re_guide = ConvBlock(2 * num_features, num_features, kernel_size=1, act_type=act_type,
                                  norm_type=norm_type)
        for idx in range(self.num_groups):
            self.upBlocks.append(
                DeconvBlock(num_features, num_features, kernel_size=kernel_size, stride=stride, padding=padding,
                            act_type=act_type, norm_type=norm_type))
            self.downBlocks.append(
                ConvBlock(num_features, num_features, kernel_size=kernel_size, stride=stride, padding=padding,
                          act_type=act_type, norm_type=norm_type, valid_padding=False))
            if idx > 0:
                self.uptranBlocks.append(
                    ConvBlock(num_features * (idx + 1), num_features, kernel_size=1, stride=1, act_type=act_type,
                              norm_type=norm_type))
                self.downtranBlocks.append(
                    ConvBlock(num_features * (idx + 1), num_features, kernel_size=1, stride=1, act_type=act_type,
                              norm_type=norm_type))

        self.compress_out = ConvBlock(self.num_groups * num_features, num_features, kernel_size=1, act_type=act_type,
                                      norm_type=norm_type)

    def forward(self, f_in, g1, g2):
        x = torch.cat((f_in, g1), dim=1)
        x = torch.cat((x, g2), dim=1)

        x = self.compress_in(x)

        lr_features = []
        hr_features = []
        lr_features.append(x)

        for idx in range(self.num_groups):
            LD_L = torch.cat(tuple(lr_features), 1)
            if idx > 0:
                LD_L = self.uptranBlocks[idx - 1](LD_L)
            LD_H = self.upBlocks[idx](LD_L)

            hr_features.append(LD_H)

            LD_H = torch.cat(tuple(hr_features), 1)
            if idx > 0:
                LD_H = self.downtranBlocks[idx - 1](LD_H)
            LD_L = self.downBlocks[idx](LD_H)

            if idx == 2:
                x_mid = torch.cat((LD_L, g2), dim=1)
                LD_L = self.re_guide(x_mid)

            lr_features.append(LD_L)

        del hr_features
        output = torch.cat(tuple(lr_features[1:]), 1)
        output = self.compress_out(output)

        return output

# ------build DRB -------- #
def conv(in_channels, out_channels, kernel_size=3, stride=1,dilation=1, bias=True, act='LeakyReLU'):
    if act is not None:
        if act == 'LeakyReLU':
            act_ = nn.LeakyReLU(0.1,inplace=True)
        elif act == 'Sigmoid':
            act_ = nn.Sigmoid()
        elif act == 'Tanh':
            act_ = nn.Tanh()

        return nn.Sequential(
            nn.Conv2d(in_channels, out_channels, kernel_size=kernel_size, stride=stride, dilation=dilation, padding=((kernel_size-1)//2)*dilation, bias=bias),
            act_
        )
    else:
        return nn.Conv2d(in_channels, out_channels, kernel_size=kernel_size, stride=stride, dilation=dilation, padding=((kernel_size-1)//2)*dilation, bias=bias)

def Conv(in_channels, out_channels, kernel_size, stride=1, dilation=1, bias=True, valid_padding=True, padding=0,
              act_type='prelu', norm_type='bn', pad_type='zero'):
    if valid_padding:
        padding = get_valid_padding(kernel_size, dilation)
    else:
        # 设置填充为1，以确保输出尺寸为64x64
        padding = 1 if kernel_size == 3 and stride == 2 else 0
    p = pad(pad_type, padding) if pad_type and pad_type != 'zero' else None
    conv = nn.Conv2d(in_channels, out_channels, kernel_size, stride=stride, padding=padding, dilation=dilation,
                     bias=bias)

    act = activation(act_type) if act_type else None
    n = norm(out_channels, norm_type) if norm_type else None
    return sequential(p, conv, n, act)

class ResnetBlock(nn.Module):
    def __init__(self, in_channels, kernel_size, dilation, bias, res_num):
        super(ResnetBlock, self).__init__()
        self.res_num = res_num
        self.stem = nn.ModuleList([
            nn.Sequential(
                nn.Conv2d(in_channels, in_channels, kernel_size=kernel_size, stride=1, dilation=dilation[0], padding=((kernel_size-1)//2)*dilation[0], bias=bias),
                nn.LeakyReLU(0.1, inplace=True),
                nn.Conv2d(in_channels, in_channels, kernel_size=kernel_size, stride=1, dilation=dilation[1], padding=((kernel_size-1)//2)*dilation[1], bias=bias),
            ) for i in range(res_num)
        ])
    def forward(self, x):

        if self.res_num > 1:
            temp = x

        for i in range(self.res_num):
            xx = self.stem[i](x)
            x = x + xx
        if self.res_num > 1:
            x = x + temp

        return x

def FAC(feat_in, kernel, ksize):
    """
    customized FAC
    """
    channels = feat_in.size(1)
    N, kernels, H, W = kernel.size()
    pad = (ksize - 1) // 2
    
    feat_in = F.pad(feat_in, (pad, pad, pad, pad), mode="replicate")
    feat_in = feat_in.unfold(2, ksize, 1).unfold(3, ksize, 1)
    feat_in = feat_in.permute(0, 2, 3, 1, 5, 4).contiguous()
    feat_in = feat_in.reshape(N, H, W, channels, ksize*ksize*4)

    if channels ==3 and kernels == ksize*ksize*4:
        ####
        kernel = kernel.permute(0, 2, 3, 1).reshape(N, H, W, 1, ksize*2, ksize*2)
        kernel = torch.cat([kernel,kernel,kernel],channels)
        kernel = kernel.permute(0, 1, 2, 3, 5, 4).reshape(N, H, W, channels, -1) 

    else:
        kernel = kernel.permute(0, 2, 3, 1).reshape(N, H, W, channels, ksize, ksize) 
        kernel = kernel.permute(0, 1, 2, 3, 5, 4).reshape(N, H, W, channels, -1) 
                
    feat_out = torch.sum(feat_in * kernel, -1)
    feat_out = feat_out.permute(0, 3, 1, 2).contiguous()
    
    return feat_out

# ------build CFNet ------ #
class CFNet(nn.Module):
    def __init__(self, in_channels=args.in_channels, out_channels=args.out_channels, num_features=args.num_features,
                 num_steps=args.num_steps, upscale_factor=args.scale,
                 act_type=args.act_type,
                 norm_type=None,
                 num_cfbs=args.num_cfbs):
        super(CFNet, self).__init__()

        if upscale_factor == 2:
            stride = 2
            padding = 2
            kernel_size = 6
        elif upscale_factor == 4:
            stride = 4
            padding = 2
            kernel_size = 8

        self.num_steps = num_steps
        self.num_features = num_features
        self.upscale_factor = upscale_factor
        self.num_cfbs = num_cfbs
        self.kernel_width = 7
        self.kernel_dim = self.kernel_width*self.kernel_width

        ks = 3
        
        # upscale_1
        self.upsample_over = nn.Upsample(scale_factor=upscale_factor, mode='bilinear', align_corners=False)

        # FEB_1
        self.conv_in_over = ConvBlock(in_channels, 4 * num_features, kernel_size=3, act_type=act_type,
                                      norm_type=norm_type)
        self.feat_in_over = ConvBlock(4 * num_features, num_features, kernel_size=1, act_type=act_type,
                                      norm_type=norm_type)

        # SRB_1
        self.srb_1 = SRB(norm_type)

        # REC_1
        self.out_over = DeconvBlock(num_features, num_features, kernel_size=kernel_size, stride=stride, padding=padding,
                                    act_type='prelu', norm_type=norm_type)
        self.conv_out_over = ConvBlock(num_features, out_channels, kernel_size=3, act_type=None, norm_type=norm_type)

        # upscale_2
        self.upsample_under = nn.Upsample(scale_factor=upscale_factor, mode='bilinear', align_corners=False)

        # FEB_2
        self.conv_in_under = ConvBlock(in_channels, 4 * num_features, kernel_size=3, act_type=None, norm_type=norm_type)
        self.feat_in_under = ConvBlock(4 * num_features, num_features, kernel_size=1, act_type=act_type,
                                       norm_type=norm_type)

        # SRB_2
        self.srb_2 = SRB(norm_type)

        # REC_2
        self.out_under = DeconvBlock(num_features, num_features, kernel_size=kernel_size, stride=stride,
                                     padding=padding,
                                     act_type=args.act_type, norm_type=norm_type)
        self.conv_out_under = ConvBlock(num_features, out_channels, kernel_size=3, act_type=None, norm_type=norm_type)

        # CFBs and RECs
        self.CFBs_1 = []
        self.CFBs_2 = []
        self.out_1 = nn.ModuleList()
        self.conv_out_1 = nn.ModuleList()
        self.out_2 = nn.ModuleList()
        self.conv_out_2 = nn.ModuleList()

        for i in range(self.num_cfbs):
            cfb_over = 'cfb_over{}'.format(i)
            cfb_under = 'cfb_under{}'.format(i)
            cfb_1 = CFB(norm_type).cuda()
            cfb_2 = CFB(norm_type).cuda()
            setattr(self, cfb_over, cfb_1)
            self.CFBs_1.append(getattr(self, cfb_over))
            setattr(self, cfb_under, cfb_2)
            self.CFBs_2.append(getattr(self, cfb_under))

            self.out_1.append(
                DeconvBlock(num_features, num_features, kernel_size=kernel_size, stride=stride, padding=padding,
                            act_type=args.act_type, norm_type=norm_type))
            self.conv_out_1.append(
                ConvBlock(num_features, out_channels, kernel_size=3, act_type=None, norm_type=norm_type))
            self.out_2.append(
                DeconvBlock(num_features, num_features, kernel_size=kernel_size, stride=stride, padding=padding,
                            act_type=args.act_type, norm_type=norm_type))
            self.conv_out_2.append(
                ConvBlock(num_features, out_channels, kernel_size=3, act_type=None, norm_type=norm_type))

        # DRB
        self.img_upsample = nn.Upsample(scale_factor=upscale_factor, mode='bilinear', align_corners=False)
        
        self.feature0 = nn.Sequential(
            Conv(in_channels, num_features, kernel_size=3, stride=2,valid_padding=False,padding=1),
            ConvBlock(num_features, num_features, kernel_size=3, stride=1),
            ConvBlock(num_features, num_features, kernel_size=3, stride=1) 
        )
        self.feature1 = nn.Sequential(
            ConvBlock(in_channels, num_features, kernel_size=3, stride=1),
            ConvBlock(num_features, num_features, kernel_size=3, stride=1),
            ConvBlock(num_features, num_features, kernel_size=3, stride=1,) 
        )
        self.kernel = nn.Sequential(
            conv(num_features*3, num_features, kernel_size=ks, stride=1),
            conv(num_features, num_features, kernel_size=ks, stride=1),
            conv(num_features, self.kernel_dim*4, kernel_size=1, stride=1,act=None)
        )
        self.res = nn.Sequential(
            Conv(num_features*3, num_features, kernel_size=ks, stride=1),
            Conv(num_features, num_features, kernel_size=ks, stride=1),
            conv(num_features, 3, kernel_size=1, stride=1) 
        )
        
    # def forward(self, lr_over, lr_under):
    def forward(self, lr_over, lr_under):
        # upsampled version of input pairs
        up_over = self.upsample_over(lr_over)
        up_under = self.upsample_under(lr_under)

        # Feature extraction block
        f_in_over = self.conv_in_over(lr_over)
        f_in_over = self.feat_in_over(f_in_over)

        f_in_under = self.conv_in_under(lr_under)
        f_in_under = self.feat_in_under(f_in_under)

        # Super-resolution block
        g_over = self.srb_1(f_in_over)
        g_under = self.srb_2(f_in_under)

        # Coupled feedback block
        g_1 = [g_over]
        g_2 = [g_under]
        for i in range(self.num_cfbs):
            g_1.append(self.CFBs_1[i](f_in_over, g_1[i], g_2[i]))
            g_2.append(self.CFBs_2[i](f_in_under, g_2[i], g_1[i]))

        #DRB 残差模块
        drb = []
        img_average = (lr_over +lr_under) / 2.0
        img_average = self.img_upsample(img_average)
        img_feature_1 = self.feature0(img_average)
        img_cat1 = torch.cat([g_2[0],g_1[0]],1)
        img_cat_1 = torch.cat([img_feature_1,img_cat1],1)
        kernel1 = self.kernel(img_cat_1)
        res1 = self.res(img_cat_1)
        fac1 = FAC(img_average,kernel1,self.kernel_width)
        fac1 = F.interpolate(fac1, scale_factor=2, mode='area')
        res1 = F.interpolate(res1, scale_factor=2, mode='area')
        drb.append(img_average + fac1 +res1)
        
        img_feature_2 = self.feature0(drb[0])
        img_cat2 = torch.cat([g_2[1],g_1[1]],1)
        img_cat_2 = torch.cat([img_feature_2,img_cat2],1)
        kernel2 = self.kernel(img_cat_2)
        res2 = self.res(img_cat_2)
        fac2 = FAC(drb[0],kernel2,self.kernel_width)
        fac2 = F.interpolate(fac2, scale_factor=2, mode='area')
        res2 = F.interpolate(res2, scale_factor=2, mode='area')
        drb.append(drb[0] + fac2 + res2)
        
        img_feature_3 = self.feature0(drb[1])
        img_cat3 = torch.cat([g_1[2],g_2[2]],1)
        img_cat_3 = torch.cat([img_feature_3,img_cat3],1)
        kernel3 = self.kernel(img_cat_3)
        res3 = self.res(img_cat_3)
        fac3 = FAC(drb[1],kernel3,self.kernel_width)
        fac3 = F.interpolate(fac3, scale_factor=2, mode='area')
        res3 = F.interpolate(res3, scale_factor=2, mode='area')
        drb.append(drb[1] + fac3 + res3)
        
        
        # Reconstruction
        res_1 = []
        res_2 = []
        res_over = self.out_over(g_over)
        res_over = self.conv_out_over(res_over)
        res_1.append(res_over)
        res_under = self.out_under(g_under)
        res_under = self.conv_out_under(res_under)
        res_2.append(res_under)
        for j in range(self.num_cfbs):
            res_o = self.out_1[j](g_1[j + 1])
            res_u = self.out_2[j](g_2[j + 1])
            res_1.append(self.conv_out_1[j](res_o))
            res_2.append(self.conv_out_2[j](res_u))

        # Output
        sr_over = []
        sr_under = []
        for k in range(self.num_cfbs + 1):
            image_over = torch.add(res_1[k], up_over)
            image_over = torch.clamp(image_over, -1.0, 1.0)
            image_over = (image_over + 1) * 127.5
            image_under = torch.add(res_2[k], up_under)
            image_under = torch.clamp(image_under, -1.0, 1.0)
            image_under = (image_under + 1) * 127.5
            sr_over.append(image_over)
            sr_under.append(image_under)

        fusion = drb[2] 
        fusion = torch.clamp(fusion,-1.0,1.0)
        fusion = (drb[2]+1)*127.5

        return sr_over,sr_under,fusion
