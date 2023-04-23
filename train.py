'''
Script for training models.
'''
import os
import time
import numpy as np
from torch import optim
import torch
import torch.utils.data
import argparse
import torch.backends.cudnn as cudnn
import random
import json
import sys

# Import dataloaders
import data.cifar10 as cifar10
import data.cifar100 as cifar100
import data.tiny_imagenet as tiny_imagenet

# Import network models
from module.resnet import resnet50, resnet110
from module.resnet_tiny_imagenet import resnet50 as resnet50_ti
from module.wide_resnet import wide_resnet_cifar
from module.densenet import densenet121


# Import train and validation utilities
from train_utils import train_single_epoch, test_single_epoch

# Import validation metrics
from metrics.metrics import test_classification_net


dataset_num_classes = {
    'cifar10': 10,
    'cifar100': 100,
    'tiny_imagenet': 200
}

dataset_loader = {
    'cifar10': cifar10,
    'cifar100': cifar100,
    'tiny_imagenet': tiny_imagenet
}


models = {
    'resnet50': resnet50,
    'resnet50_ti': resnet50_ti,
    'resnet110': resnet110,
    'wide_resnet': wide_resnet_cifar,
    'densenet121': densenet121
}



def parseArgs():
    default_dataset = 'cifar100'
    dataset_root = './'
    train_batch_size = 128
    test_batch_size = 128
    learning_rate = 0.1
    momentum = 0.9
    optimiser = "sgd"
    loss = "cross_entropy"
    gamma = 1.0
    gamma2 = 1.0
    gamma3 = 1.0
    lamda = 1.0
    weight_decay = 5e-4
    log_interval = 50
    save_interval = 50
    save_loc = './run/'
    model_name = None
    saved_model_name = "resnet50_cross_entropy_350.model"
    load_loc = './'
    model = "resnet50"
    epoch = 350
    first_milestone = 150 #Milestone for change in lr
    second_milestone = 250 #Milestone for change in lr
    gamma_schedule_step1 = 100
    gamma_schedule_step2 = 250

    parser = argparse.ArgumentParser(
        description="Training for calibration.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--dataset", type=str, default=default_dataset,
                        dest="dataset", help='dataset to train on')
    parser.add_argument("--dataset-root", type=str, default=dataset_root,
                        dest="dataset_root", help='root path of the dataset (for tiny imagenet)')
    parser.add_argument("--data-aug", action="store_true", dest="data_aug")
    parser.add_argument("--parallel", action="store_true", default=False)
    parser.add_argument("--deterministic", action="store_true", default=False)
    parser.set_defaults(data_aug=True)

    parser.add_argument("-g", action="store_true", dest="gpu",
                        help="Use GPU")
    parser.set_defaults(gpu=True)
    parser.add_argument("--load", action="store_true", dest="load",
                        help="Load from pretrained model")
    parser.set_defaults(load=False)
    parser.add_argument("-b", type=int, default=train_batch_size,
                        dest="train_batch_size", help="Batch size")
    parser.add_argument("-tb", type=int, default=test_batch_size,
                        dest="test_batch_size", help="Test Batch size")
    parser.add_argument("-e", type=int, default=epoch, dest="epoch",
                        help='Number of training epochs')
    parser.add_argument("--lr", type=float, default=learning_rate,
                        dest="learning_rate", help='Learning rate')
    parser.add_argument("--mom", type=float, default=momentum,
                        dest="momentum", help='Momentum')
    parser.add_argument("--nesterov", action="store_true", dest="nesterov",
                        help="Whether to use nesterov momentum in SGD")
    parser.set_defaults(nesterov=False)
    parser.add_argument("--decay", type=float, default=weight_decay,
                        dest="weight_decay", help="Weight Decay")
    parser.add_argument("--opt", type=str, default=optimiser,
                        dest="optimiser",
                        help='Choice of optimisation algorithm')

    parser.add_argument("--loss", type=str, default=loss, dest="loss_function",
                        help="Loss function to be used for training")
    parser.add_argument("--loss-mean", action="store_true", dest="loss_mean",
                        help="whether to take mean of loss instead of sum to train")
    parser.set_defaults(loss_mean=False)
    parser.add_argument("--gamma", type=float, default=gamma,
                        dest="gamma", help="Gamma for focal components")
    parser.add_argument("--gamma2", type=float, default=gamma2,
                        dest="gamma2", help="Gamma for different focal components")
    parser.add_argument("--gamma3", type=float, default=gamma3,
                        dest="gamma3", help="Gamma for different focal components")
    parser.add_argument("--lamda", type=float, default=lamda,
                        dest="lamda", help="Regularization factor")
    parser.add_argument("--gamma-schedule", type=int, default=0,
                        dest="gamma_schedule", help="Schedule gamma or not")
    parser.add_argument("--gamma-schedule-step1", type=int, default=gamma_schedule_step1,
                        dest="gamma_schedule_step1", help="1st step for gamma schedule")
    parser.add_argument("--gamma-schedule-step2", type=int, default=gamma_schedule_step2,
                        dest="gamma_schedule_step2", help="2nd step for gamma schedule")

    parser.add_argument("--log-interval", type=int, default=log_interval,
                        dest="log_interval", help="Log Interval on Terminal")
    parser.add_argument("--save-interval", type=int, default=save_interval,
                        dest="save_interval", help="Save Interval on Terminal")
    parser.add_argument("--saved_model_name", type=str, default=saved_model_name,
                        dest="saved_model_name", help="file name of the pre-trained model")
    parser.add_argument("--save-path", type=str, default=save_loc,
                        dest="save_loc",
                        help='Path to export the model')
    parser.add_argument("--model-name", type=str, default=model_name,
                        dest="model_name",
                        help='name of the model')
    parser.add_argument("--load-path", type=str, default=load_loc,
                        dest="load_loc",
                        help='Path to load the model from')

    parser.add_argument("--model", type=str, default=model, dest="model",
                        help='Model to train')
    parser.add_argument("--first-milestone", type=int, default=first_milestone,
                        dest="first_milestone", help="First milestone to change lr")
    parser.add_argument("--second-milestone", type=int, default=second_milestone,
                        dest="second_milestone", help="Second milestone to change lr")

    parser.add_argument("--seed", type=int, default=20)

    return parser.parse_args()


if __name__ == "__main__":
    args = parseArgs()
    experiment_name = '{}-{}-{}'.format(args.model, args.dataset, args.weight_decay)
    args.save_loc = '../checkpoints/{}-{}-{}/'.format(
        experiment_name, time.strftime("%Y%m%d-%H%M%S"), np.random.randint(100)
    )
    if not os.path.exists(args.save_loc):
        os.makedirs(args.save_loc)


    def setup_seed(seed):
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
        np.random.seed(seed)
        random.seed(seed)
        if args.deterministic:
            print('deterministic')
            torch.backends.cudnn.deterministic = True

    setup_seed(args.seed)

    cuda = False
    if (torch.cuda.is_available() and args.gpu):
        cuda = True
    device = torch.device("cuda" if cuda else "cpu")
    print("CUDA set: " + str(cuda))


    num_classes = dataset_num_classes[args.dataset]

    # Choosing the model to train
    net = models[args.model](num_classes=num_classes)

    # Setting model name
    if args.model_name is None:
        args.model_name = args.model


    if args.gpu is True:
        net.cuda()

    start_epoch = 0
    num_epochs = args.epoch
    if args.load:
        net.load_state_dict(torch.load(args.save_loc + args.saved_model_name))
        start_epoch = int(args.saved_model_name[args.saved_model_name.rfind('_')+1:args.saved_model_name.rfind('.model')])

    if args.optimiser == "sgd":
        opt_params = net.parameters()
        optimizer = optim.SGD(opt_params,
                              lr=args.learning_rate,
                              momentum=args.momentum,
                              weight_decay=args.weight_decay,
                              nesterov=args.nesterov)
    elif args.optimiser == "adam":
        opt_params = net.parameters()
        optimizer = optim.Adam(opt_params,
                               lr=args.learning_rate,
                               weight_decay=args.weight_decay)
    scheduler = optim.lr_scheduler.MultiStepLR(optimizer, milestones=[args.first_milestone, args.second_milestone], gamma=0.1)

    if (args.dataset == 'tiny_imagenet'):
        train_loader = dataset_loader[args.dataset].get_data_loader(
            root=args.dataset_root,
            split='train',
            batch_size=args.train_batch_size,
            pin_memory=args.gpu)

        val_loader = dataset_loader[args.dataset].get_data_loader(
            root=args.dataset_root,
            split='val',
            batch_size=args.test_batch_size,
            pin_memory=args.gpu)

        test_loader = dataset_loader[args.dataset].get_data_loader(
            root=args.dataset_root,
            split='val',
            batch_size=args.test_batch_size,
            pin_memory=args.gpu)
    else:
        train_loader, val_loader = dataset_loader[args.dataset].get_train_valid_loader(
            batch_size=args.train_batch_size,
            augment=args.data_aug,
            random_seed=1,
            pin_memory=args.gpu
        )

        test_loader = dataset_loader[args.dataset].get_test_loader(
            batch_size=args.test_batch_size,
            pin_memory=args.gpu
        )

    training_set_loss = {}
    val_set_loss = {}
    test_set_loss = {}
    val_set_err = {}

    for epoch in range(0, start_epoch):
        scheduler.step()

    best_val_acc = 0
    for epoch in range(start_epoch, num_epochs):
        if (args.loss_function == 'focal_loss' and args.gamma_schedule == 1):
            if (epoch < args.gamma_schedule_step1):
                gamma = args.gamma
            elif (epoch >= args.gamma_schedule_step1 and epoch < args.gamma_schedule_step2):
                gamma = args.gamma2
            else:
                gamma = args.gamma3
        else:
            gamma = args.gamma

        train_loss = train_single_epoch(epoch,
                                        net,
                                        train_loader,
                                        optimizer,
                                        device,
                                        loss_function=args.loss_function,
                                        gamma=gamma,
                                        lamda=args.lamda,
                                        loss_mean=args.loss_mean)
        scheduler.step()

        val_loss = test_single_epoch(epoch,
                                     net,
                                     val_loader,
                                     device,
                                     loss_function=args.loss_function,
                                     gamma=gamma,
                                     lamda=args.lamda)

        test_loss = test_single_epoch(epoch,
                                      net,
                                      test_loader,
                                      device,
                                      loss_function=args.loss_function,
                                      gamma=gamma,
                                      lamda=args.lamda)
        _, val_acc, _, _, _, _ = test_classification_net(net, val_loader, device)

        # training_set_loss[epoch] = train_loss
        val_set_loss[epoch] = val_loss
        test_set_loss[epoch] = test_loss
        val_set_err[epoch] = 1 - val_acc


        if val_acc > best_val_acc:
            best_val_acc = val_acc
            print('New best error: %.4f' % (1 - best_val_acc))
            save_name = args.save_loc + \
                        args.model_name + '_' + \
                        'cross_entropy' + \
                        '_best_' + \
                        str(epoch + 1) + '.model'
            torch.save(net.state_dict(), save_name)

        if (epoch + 1) % args.save_interval == 0:
            save_name = args.save_loc + \
                        args.model_name + '_' + \
                        'cross_entropy' + \
                        '_' + str(epoch + 1) + '.model'
            torch.save(net.state_dict(), save_name)


    with open(save_name[:save_name.rfind('_')] + '_train_loss.json', 'a') as f:
        json.dump(training_set_loss, f)

    with open(save_name[:save_name.rfind('_')] + '_val_loss.json', 'a') as fv:
        json.dump(val_set_loss, fv)

    with open(save_name[:save_name.rfind('_')] + '_test_loss.json', 'a') as ft:
        json.dump(test_set_loss, ft)

    with open(save_name[:save_name.rfind('_')] + '_val_error.json', 'a') as ft:
        json.dump(val_set_err, ft)