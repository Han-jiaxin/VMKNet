import torch
import torch.nn as nn
from bakebone.pvtv2 import pvt_v2_b2
import torch.nn.functional as F
from model.MultiScaleAttention import Block_model

k=32
focal_num = 2

class BasicConv2d(nn.Module):
    def __init__(self, in_planes, out_planes, kernel_size, stride=1, padding=0, dilation=1):
        super(BasicConv2d, self).__init__()
        self.conv = nn.Conv2d(in_planes, out_planes,
                              kernel_size=kernel_size, stride=stride,
                              padding=padding, dilation=dilation, bias=False)
        self.bn = nn.BatchNorm2d(out_planes)
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x):
        x = self.conv(x)
        x = self.bn(x)
        return x

class RFB_modified(nn.Module):
    def __init__(self, in_channel, out_channel):
        super(RFB_modified, self).__init__()
        self.relu = nn.ReLU(True)
        self.branch0 = nn.Sequential(
            BasicConv2d(in_channel, out_channel, 1),
        )
        self.branch1 = nn.Sequential(
            BasicConv2d(in_channel, out_channel, 1),
            BasicConv2d(out_channel, out_channel, kernel_size=(1, 3), padding=(0, 1)),
            BasicConv2d(out_channel, out_channel, kernel_size=(3, 1), padding=(1, 0)),
            BasicConv2d(out_channel, out_channel, 3, padding=3, dilation=3)
        )
        self.branch2 = nn.Sequential(
            BasicConv2d(in_channel, out_channel, 1),
            BasicConv2d(out_channel, out_channel, kernel_size=(1, 5), padding=(0, 2)),
            BasicConv2d(out_channel, out_channel, kernel_size=(5, 1), padding=(2, 0)),
            BasicConv2d(out_channel, out_channel, 3, padding=5, dilation=5)
        )
        self.branch3 = nn.Sequential(
            BasicConv2d(in_channel, out_channel, 1),
            BasicConv2d(out_channel, out_channel, kernel_size=(1, 7), padding=(0, 3)),
            BasicConv2d(out_channel, out_channel, kernel_size=(7, 1), padding=(3, 0)),
            BasicConv2d(out_channel, out_channel, 3, padding=7, dilation=7)
        )
        self.conv_cat = BasicConv2d(4*out_channel, out_channel, 3, padding=1)
        self.conv_res = BasicConv2d(in_channel, out_channel, 1)

    def forward(self, x):
        x0 = self.branch0(x)
        x1 = self.branch1(x)
        x2 = self.branch2(x)
        x3 = self.branch3(x)
        x_cat = self.conv_cat(torch.cat((x0, x1, x2, x3), 1))

        x = self.relu(x_cat + self.conv_res(x))
        return x

class SpatialAttention(nn.Module):
    def __init__(self, kernel_size=7):
        super(SpatialAttention, self).__init__()

        assert kernel_size in (3, 7), 'kernel size must be 3 or 7'
        padding = 3 if kernel_size == 7 else 1

        self.conv1 = nn.Conv2d(1, 1, kernel_size, padding=padding, bias=False)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        max_out, _ = torch.max(x, dim=1, keepdim=True)
        x = max_out
        x = self.conv1(x)
        return self.sigmoid(x)

class ChannelAttention(nn.Module):
    def __init__(self, in_planes, ratio=16):
        super(ChannelAttention, self).__init__()
        
        self.max_pool = nn.AdaptiveMaxPool2d(1)

        self.fc1   = nn.Conv2d(in_planes, in_planes // 16, 1, bias=False)
        self.relu1 = nn.ReLU()
        self.fc2   = nn.Conv2d(in_planes // 16, in_planes, 1, bias=False)

        self.sigmoid = nn.Sigmoid()
    def forward(self, x):

        max_out = self.fc2(self.relu1(self.fc1(self.max_pool(x))))
        out = max_out
        return self.sigmoid(out)*x
    
class decoder_mini(nn.Module):
    def __init__(self, channel=32):
        super(decoder_mini, self).__init__()
        
        self.CA1_1 = ChannelAttention(64)
        self.conv3x3 = nn.Conv2d(64,k,kernel_size=3,padding=1)
        self.ba1_1 = nn.BatchNorm2d(k)
        self.relu1_1 = nn.ReLU()
        self.conv3x3_1 = nn.Conv2d(32,k,kernel_size=3,padding=1)
        self.conv_out_1 = nn.Conv2d(k,1,kernel_size=1)
        self.upsample2 = nn.Upsample(scale_factor=4, mode='bilinear', align_corners=True)
        self.upsample3 = nn.Upsample(scale_factor=8, mode='bilinear', align_corners=True)
        
    
    def forward(self,f1,f4):
        
        f1_1 = self.relu1_1(self.ba1_1(self.conv3x3(self.CA1_1(torch.cat((f1,self.upsample3(f4)),1)))))
        
        s2_x_focal_1 = self.upsample2(self.conv_out_1(self.conv3x3_1(f1_1)))
        sal_x = torch.sigmoid(s2_x_focal_1)
        
        return sal_x

class select(nn.Module):
    def __init__(self, channel=32):
        super(select, self).__init__()
    
    def forward(self,x,x_focal,sal_x):
        bz= x.shape[0]
        
        x_focal= torch.cat(torch.chunk(x_focal, 12, dim=0), dim=1)
        
        x_focal_o= x_focal.unsqueeze(dim=1)
        x_focal_o= torch.cat(torch.chunk(x_focal_o, 12, dim=2), dim=1)
        
        image= torch.mul(x, sal_x)
        focal = torch.mul(x_focal,sal_x )
        
        focal1, focal2, focal3, focal4, focal5, focal6, focal7, focal8, focal9, focal10, focal11, focal12= torch.chunk(focal, 12, dim=1) 
        
        mae1= torch.mean(torch.abs(torch.sub(image, focal1)), dim=[1,2,3])
        mae2= torch.mean(torch.abs(torch.sub(image,focal2)), dim=[1,2,3])
        mae3= torch.mean(torch.abs(torch.sub(image, focal3)), dim=[1,2,3])
        mae4= torch.mean(torch.abs(torch.sub(image, focal4)), dim=[1,2,3])
        mae5= torch.mean(torch.abs(torch.sub(image, focal5)), dim=[1,2,3])
        mae6= torch.mean(torch.abs(torch.sub(image, focal6)), dim=[1,2,3])
        mae7= torch.mean(torch.abs(torch.sub(image, focal7)), dim=[1,2,3])
        mae8= torch.mean(torch.abs(torch.sub(image, focal8)), dim=[1,2,3])
        mae9= torch.mean(torch.abs(torch.sub(image, focal9)), dim=[1,2,3])
        mae10= torch.mean(torch.abs(torch.sub(image, focal10)), dim=[1,2,3])
        mae11= torch.mean(torch.abs(torch.sub(image, focal11)), dim=[1,2,3])
        mae12= torch.mean(torch.abs(torch.sub(image, focal12)), dim=[1,2,3])
        
        w = torch.cat([mae1.unsqueeze(dim=1), mae2.unsqueeze(dim=1), mae3.unsqueeze(dim=1), mae4.unsqueeze(dim=1), mae5.unsqueeze(dim=1), mae6.unsqueeze(dim=1), mae7.unsqueeze(dim=1), mae8.unsqueeze(dim=1), mae9.unsqueeze(dim=1), mae10.unsqueeze(dim=1), mae11.unsqueeze(dim=1), mae12.unsqueeze(dim=1)], dim=1)
        
        numbers = self.select_weights(w,focal_num)
    
        chunks = torch.chunk(numbers, focal_num, dim=1)
        result_list = []

        
        for i, chunk in enumerate(chunks):
            
            globals()[f'g{i+1}'] = x_focal_o[torch.arange(bz), chunk.squeeze(dim=1)]
            
            result_list.append(globals()[f'g{i+1}'])

        return torch.cat(result_list, dim=0)
        
    def select_weights(self, input_weights,focal_n):
        batch_size, num_weights = input_weights.size()

        max_variance = torch.zeros(batch_size, device=input_weights.device)
        selected_indices = torch.zeros((batch_size, focal_n), device=input_weights.device)

        for batch_idx in range(batch_size):
            batch_weights = input_weights[batch_idx]

            for i in range(num_weights):
                for j in range(i + 1, num_weights):
                    
                    selected = torch.tensor([i, j], device=input_weights.device)
                    selected_weights = torch.index_select(batch_weights, 0, selected)
                    variance = torch.var(selected_weights)

                    if variance > max_variance[batch_idx]:
                        max_variance[batch_idx] = variance
                        selected_indices[batch_idx] = selected

        return selected_indices.to(torch.long)

class Refine(nn.Module):
    def __init__(self,in_channels):
        super(Refine,self).__init__()
        self.conv = nn.Conv2d(in_channels,in_channels,kernel_size=3,padding=1)
    def forward(self,x):
        out = x+self.conv(x)
        return out

class Rgb_guide_sa2(nn.Module):
    def __init__(self):
        super(Rgb_guide_sa2, self).__init__()
        self.SA = SpatialAttention()
        self.SA2 = SpatialAttention()
        
    def forward(self, rgb, focal):
        rgb_sa = self.SA(rgb)
        focal_sa = self.SA2(focal) #[4,1,64,64]
        
        focal_new = focal + torch.mul(rgb_sa, focal) #[4, 96, 64, 64]
        x_new = rgb + torch.mul(focal_sa,rgb)
        x_new = torch.cat((x_new,x_new),dim=1)   #[4, 32, 64, 64]
        
        fuse = focal_new  + x_new
        
        return fuse

class BConv3(nn.Module):
    def __init__(self,input_channel,output_channel,kernel_size,padding):
        super().__init__()
        self.relu = nn.ReLU(inplace=False)
        self.conv1 = nn.Conv2d(input_channel,output_channel,kernel_size=kernel_size,padding=padding)
        self.bn1 = nn.BatchNorm2d(output_channel)
        self.conv2 = nn.Conv2d(output_channel, output_channel, kernel_size=kernel_size,padding=padding)
        self.bn2 = nn.BatchNorm2d(output_channel)
        self.conv3 = nn.Conv2d(output_channel, output_channel, kernel_size=kernel_size,padding=padding)
        self.bn3 = nn.BatchNorm2d(output_channel)

    def forward(self,x):
        input = x
        out = self.relu(self.bn1(self.conv1(input)))
        out = self.relu(self.bn2(self.conv2(out)))
        out = self.relu(self.bn3(self.conv3(out)))
        return out

class decoder(nn.Module):
    def __init__(self):
        super(decoder,self).__init__()
        bgm = 64
        self.upsample5 = nn.Upsample(scale_factor=32, mode='bilinear', align_corners=True)
        self.upsample4 = nn.Upsample(scale_factor=16, mode='bilinear', align_corners=True)
        self.upsample3 = nn.Upsample(scale_factor=8, mode='bilinear', align_corners=True)
        self.upsample2 = nn.Upsample(scale_factor=4, mode='bilinear', align_corners=True)
        self.upsample1 = nn.Upsample(scale_factor=2, mode='bilinear', align_corners=True)
        
        self.conv_ca0 = nn.Conv2d(bgm*2,bgm,kernel_size=3,padding=1)
        self.ba_ca0 = nn.BatchNorm2d(bgm)
        self.relu_ca0 = nn.ReLU()

        self.conv_ca1 = nn.Conv2d(bgm*2,bgm,kernel_size=3,padding=1)
        self.ba_ca1 = nn.BatchNorm2d(bgm)
        self.relu_ca1 = nn.ReLU()

        self.conv_ca2 = nn.Conv2d(bgm*2,bgm,kernel_size=3,padding=1)
        self.ba_ca2 = nn.BatchNorm2d(bgm)
        self.relu_ca2 = nn.ReLU()

        self.ref = Refine(bgm)
        self.conv_sal_re = nn.Conv2d(bgm,1,kernel_size=3,padding=1)
        self.conv_sal0 = nn.Conv2d(bgm,1,kernel_size=3,padding=1)
        self.conv_sal1 = nn.Conv2d(bgm,1,kernel_size=3,padding=1)
        self.conv_sal2 = nn.Conv2d(bgm,1,kernel_size=3,padding=1)
        self.conv_sal3 = nn.Conv2d(bgm,1,kernel_size=3,padding=1)
        self.conv_sal4 = nn.Conv2d(bgm,1,kernel_size=3,padding=1)
    
    def forward(self,f0,f1,f2,f3):
         
        C3 = f3
        C2 = self.relu_ca2(self.ba_ca2(self.conv_ca2(torch.cat((self.upsample1(C3),f2),1))))

        C1 = self.relu_ca1(self.ba_ca1(self.conv_ca1(torch.cat((self.upsample1(C2),f1),1))))
        C0 = self.relu_ca0(self.ba_ca0(self.conv_ca0(torch.cat((self.upsample1(C1),f0),1))))
        
        S_re = self.ref(C0)
        sal_re = self.upsample2(self.conv_sal_re(S_re))
        sal0 = self.upsample2(self.conv_sal0(C0))
        sal1 = self.upsample3(self.conv_sal1(C1))
        sal2 = self.upsample4(self.conv_sal2(C2))
        sal3 = self.upsample5(self.conv_sal3(C3))
        
        
        return F.sigmoid(sal_re),F.sigmoid(sal0),  F.sigmoid(sal1),F.sigmoid(sal2),F.sigmoid(sal3),

class edge(nn.Module):
    def __init__(self):
        super().__init__()
        self.relu = nn.ReLU(inplace=False)
        chanell_n = 32
        self.edge_extract_1 = BConv3(3 * chanell_n , chanell_n , 3, 1)
        self.edge_extract_2 = BConv3(3 * chanell_n , chanell_n , 3, 1)
        self.edge_extract_3 = BConv3(3 * chanell_n , chanell_n , 3, 1)
        self.edge_extract_4 = BConv3(2*chanell_n , chanell_n , 3, 1)
        self.edge_score_1 = nn.Conv2d(chanell_n , 1, 1, 1)
        self.edge_score_2 = nn.Conv2d(chanell_n , 1, 1, 1)
        self.edge_score_3 = nn.Conv2d(chanell_n , 1, 1, 1)
        self.edge_score_4 = nn.Conv2d(chanell_n , 1, 1, 1)
        

    def forward(self,f1,f2,f3,f4):
        
        e4 = self.edge_extract_4(f4)
        
        e4_1 = F.interpolate(e4, scale_factor=2, mode='bilinear', align_corners=True)  #[ba,96,32,32]
        e3 = self.edge_extract_3(torch.cat((f3,e4_1),dim=1))
        e3_1 = F.interpolate(e3, scale_factor=2, mode='bilinear', align_corners=True)#[2,64,64,64]
        e2 = self.edge_extract_2(torch.cat((f2,e3_1),dim=1))
        e2_1 = F.interpolate(e2, scale_factor=2, mode='bilinear', align_corners=True)#[2,64,64,64]
        e1 = self.edge_extract_1(torch.cat((f1,e2_1),dim=1))
        

        edge_1 = self.edge_score_1(e1)
        edge_2 = self.edge_score_2(e2)
        edge_3 = self.edge_score_1(e3)
        edge_4 = self.edge_score_2(e4)

        edg_1=F.interpolate(edge_1, (256, 256), mode='bilinear', align_corners=True)
        edg_2=F.interpolate(edge_2, (256, 256), mode='bilinear', align_corners=True)
        edg_3=F.interpolate(edge_3, (256, 256), mode='bilinear', align_corners=True)
        edg_4=F.interpolate(edge_4, (256, 256), mode='bilinear', align_corners=True)

        pre_edge = torch.cat((edg_1,edg_2,edg_3,edg_4),dim=0)
        return pre_edge

class model(nn.Module):
    def __init__(self):
        super(model, self).__init__()
        #focal
        self.focal_encoder = pvt_v2_b2()
        self.rfb4 = RFB_modified(512, 32)
        self.rfb3 = RFB_modified(320, 32)
        self.rfb2 = RFB_modified(128, 32)
        self.rfb1 = RFB_modified(64, 32)
        
        self.bn1 = nn.BatchNorm2d(96, eps=1e-05, momentum=0.1, affine=True)
        self.bn2 = nn.BatchNorm2d(1, eps=1e-05, momentum=0.1, affine=True)
        
        #rgb
        self.rgb_encoder = pvt_v2_b2()
        self.rfb33 = RFB_modified(512, 32)
        self.rfb22 = RFB_modified(320, 32)
        self.rfb11 = RFB_modified(128, 32)
        self.rfb00 = RFB_modified(64, 32)

        self.rgs = nn.ModuleList()
        for i in range(4):
            self.rgs.append(Rgb_guide_sa2())
        
        #fuse
        self.decoder_mini = decoder_mini()
        self.decoder = decoder()
        self.select = select()
        
        self.edge = edge()

        self.mhsa_rgb2 = Block_model(32, 4)
        self.mhsa_rgb3 = Block_model(32, 4)
        self.mhsa_f2 = Block_model(32*2, 4)
        self.mhsa_f3 = Block_model(32*2, 4)

    def forward(self, y,x):
        #rgb
        rgb = y
        y = self.rgb_encoder(y)
        y[0] = self.rfb00(y[0])  # [ba, 32, 64, 64]
        y[1] = self.rfb11(y[1])  # [ba, 32, 32, 32]
        y[2] = self.rfb22(y[2])  # [ba, 32, 16, 16]
        y[3] = self.rfb33(y[3])  # [ba, 32, 8, 8]
        
        sal_x = self.decoder_mini(y[0],y[3])
        
        focal = self.select(rgb,x,sal_x)
        
        
        x = self.focal_encoder(focal)
        x0f = self.rfb1(x[0])                        # [ba*2, 32, 64, 64]
        x1f = self.rfb2(x[1])                        # [ba*2, 32, 32, 32]
        x2f = self.rfb3(x[2])                        # [ba*2, 32, 16, 16]
        x3f = self.rfb4(x[3])                        # [ba*2, 32, 8, 8]

        #reshape
        
        ba = focal.size()[0]//focal_num
        
        x2_a = x2q_sal.reshape(ba, focal_num*32, 16*16).permute(0, 2, 1)
        x3_a = x3q_sal.reshape(ba, focal_num*32, 8 * 8).permute(0, 2, 1)
        y2_a = y[2].reshape(ba,32,16*16).permute(0, 2, 1)  #[ba,256,32]
        y3_a = y[3].reshape(ba,32,8*8).permute(0, 2, 1)    #[ba,64,32]

        
        y2_en = self.mhsa_rgb2(y2_a)               
        y3_en = self.mhsa_rgb3(y3_a)
        x2_en = self.mhsa_f2(x2_a)                 
        x3_en = self.mhsa_f3(x3_a)
        
        y2_en= y2_en.reshape(ba,16,16,32).permute(0, 3, 1,2)    
        y3_en = y3_en.reshape(ba,8,8,32).permute(0, 3, 1,2)     
        x2_en= x2_en.reshape(ba,16,16,32*2).permute(0, 3, 1,2)  
        x3_en = x3_en.reshape(ba,8,8,32*2).permute(0, 3, 1,2)   

        # BGM
        xy0_fuse = self.rgs[0](y[0], x[0])

        xy1_fuse = self.rgs[1](y[1], x[1])
        
        xy2_fuse = self.rgs[2](y2_en,x2_en)
        
        xy3_fuse = self.rgs[3](y3_en,x3_en)


        fuse_edge = self.edge(xy0_fuse,xy1_fuse,xy2_fuse,xy3_fuse)  

        sal_re,sal0,sal1,sal2,sal3, = self.decoder(xy0_fuse,xy1_fuse,xy2_fuse,xy3_fuse)

        return sal_re,sal0,sal1,sal2,sal3,fuse_edge,sal_x
