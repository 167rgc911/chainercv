from __future__ import division

import numpy as np

import chainer
import chainer.functions as F
from chainer import initializers
import chainer.links as L

from chainercv.links.model.resnet.building_block import BuildingBlock
from chainercv.links import Conv2DBNActiv
from chainercv.links import PickableSequentialChain
from chainercv.utils import download_model


# RGB order
# This is channel wise mean of mean image distributed at
# https://github.com/KaimingHe/deep-residual-networks
_imagenet_mean = np.array(
    [123.15163084, 115.90288257, 103.0626238],
    dtype=np.float32)[:, np.newaxis, np.newaxis]


class ResNet(PickableSequentialChain):

    """Base class for ResNet Network.

    This is a feature extraction link.
    The network can choose output layers from set of all
    intermediate layers.
    The attribute :obj:`pick` is the names of the layers that are going
    to be picked by :meth:`__call__`.
    The attribute :obj:`layer_names` is the names of all layers
    that can be picked.

    Examples:

        >>> model = ResNet50()
        # By default, __call__ returns a probability score (after Softmax).
        >>> prob = model(imgs)
        >>> model.pick = 'res5'
        # This is layer res5
        >>> res5 = model(imgs)
        >>> model.pick = ['res5', 'fc6']
        >>> # These are layers res5 and fc6.
        >>> res5, fc6 = model(imgs)

    .. seealso::
        :class:`chainercv.links.model.PickableSequentialChain`

    When :obj:`pretrained_model` is the path of a pre-trained chainer model
    serialized as a :obj:`.npz` file in the constructor, this chain model
    automatically initializes all the parameters with it.
    When a string in the prespecified set is provided, a pretrained model is
    loaded from weights distributed on the Internet.
    The list of pretrained models supported are as follows:

    * :obj:`imagenet`: Loads weights trained with ImageNet and distributed \
        at `Model Zoo \
        <https://github.com/BVLC/caffe/wiki/Model-Zoo>`_.
        This is only supported when :obj:`fb_resnet=False`.

    Args:
        model_name (str): Name of the resnet model to instantiate.
        n_class (int): The number of classes. If :obj:`None`,
            the default values are used.
            If a supported pretrained model is used,
            the number of classes used to train the pretrained model
            is used. Otherwise, the number of classes in ILSVRC 2012 dataset
            is used.
        pretrained_model (str): The destination of the pre-trained
            chainer model serialized as a :obj:`.npz` file.
            If this is one of the strings described
            above, it automatically loads weights stored under a directory
            :obj:`$CHAINER_DATASET_ROOT/pfnet/chainercv/models/`,
            where :obj:`$CHAINER_DATASET_ROOT` is set as
            :obj:`$HOME/.chainer/dataset` unless you specify another value
            by modifying the environment variable.
        mean (numpy.ndarray): A mean value. If :obj:`None`,
            the default values are used.
            If a supported pretrained model is used,
            the mean value used to train the pretrained model is used.
            Otherwise, the mean value calculated from ILSVRC 2012 dataset
            is used.
        initialW (callable): Initializer for the weights.
        fb_resnet (bool): If :obj:`True`, use Facebook ResNet
            architecture. Otherwise, use the architecture presented
            by `the original ResNet paper \
            <https://arxiv.org/pdf/1512.03385.pdf>`_.
            This option changes where to apply strided convolution.

    """

    _blocks = {
        'resnet18': [2, 2, 2, 2],
        'resnet50': [3, 4, 6, 3],
        'resnet101': [3, 4, 23, 3],
        'resnet152': [3, 8, 36, 3]
    }

    _he_models = {
        'resnet18': {},
        'resnet50': {
            'imagenet': {
                'n_class': 1000,
                'url': 'https://github.com/yuyu2172/share-weights/releases/'
                'download/0.0.3/resnet50_imagenet_convert_2017_07_06.npz',
                'mean': _imagenet_mean
            },
        },
        'resnet101': {
            'imagenet': {
                'n_class': 1000,
                'url': 'https://github.com/yuyu2172/share-weights/releases/'
                'download/0.0.3/resnet101_imagenet_convert_2017_07_06.npz',
                'mean': _imagenet_mean
            },
        },
        'resnet152': {
            'imagenet': {
                'n_class': 1000,
                'url': 'https://github.com/yuyu2172/share-weights/releases/'
                'download/0.0.3/resnet152_imagenet_convert_2017_07_06.npz',
                'mean': _imagenet_mean
            },
        }
    }

    _fb_models = {
        'resnet18': dict(),
        'resnet50': dict(),
        'resnet101': dict(),
        'resnet152': dict()
    }

    def __init__(self, model_name,
                 n_class=None,
                 pretrained_model=None,
                 mean=None, initialW=None, fb_resnet=False):
        if fb_resnet:
            if pretrained_model == 'imagenet':
                raise ValueError(
                    'Pretrained weights for Facebook ResNet models '
                    'are not supported.')
            _models = self._fb_models[model_name]
        else:
            _models = self._he_models[model_name]
        block = self._blocks[model_name]

        if n_class is None:
            if pretrained_model in _models:
                n_class = _models[pretrained_model]['n_class']
            else:
                n_class = 1000

        if mean is None:
            if pretrained_model in _models:
                mean = _models[pretrained_model]['mean']
            else:
                mean = _imagenet_mean
        self.mean = mean

        if initialW is None:
            conv_initialW = HeNormal(scale=1., fan_option='fan_out')
            fc_initialW = initializers.Normal(scale=0.01)
        if pretrained_model:
            # As a sampling process is time-consuming,
            # we employ a zero initializer for faster computation.
            initialW = initializers.constant.Zero()
        kwargs = {'initialW': conv_initialW, 'stride_first': not fb_resnet}

        super(ResNet, self).__init__()
        with self.init_scope():
            self.conv1 = Conv2DBNActiv(None, 64, 7, 2, 3, nobias=fb_resnet,
                                       initialW=initialW)
            self.pool1 = lambda x: F.max_pooling_2d(x, ksize=3, stride=2)
            self.res2 = BuildingBlock(block[0], None, 64, 256, 1, **kwargs)
            self.res3 = BuildingBlock(block[1], None, 128, 512, 2, **kwargs)
            self.res4 = BuildingBlock(block[2], None, 256, 1024, 2, **kwargs)
            self.res5 = BuildingBlock(block[3], None, 512, 2048, 2, **kwargs)
            self.pool5 = _global_average_pooling_2d
            self.fc6 = L.Linear(None, n_class, initialW=fc_initialW)
            self.prob = F.softmax

        if pretrained_model in _models:
            path = download_model(_models[pretrained_model]['url'])
            chainer.serializers.load_npz(path, self)
        elif pretrained_model:
            chainer.serializers.load_npz(pretrained_model, self)


def _global_average_pooling_2d(x):
    n, channel, rows, cols = x.data.shape
    h = F.average_pooling_2d(x, (rows, cols), stride=1)
    h = h.reshape((n, channel))
    return h


class ResNet18(ResNet):

    """ResNet-18 Network.

    Please consult the documentation for :class:`ResNet`.

    .. seealso::
        :class:`chainercv.links.model.resnet.ResNet`

    """

    def __init__(self, n_class=None, pretrained_model=None,
                 mean=None, initialW=None, fb_resnet=False):
        super(ResNet18, self).__init__(
            'resnet18', n_class, pretrained_model,
            mean, initialW, fb_resnet)


class ResNet50(ResNet):

    """ResNet-50 Network.

    Please consult the documentation for :class:`ResNet`.

    .. seealso::
        :class:`chainercv.links.model.resnet.ResNet`

    """

    def __init__(self, n_class=None, pretrained_model=None,
                 mean=None, initialW=None, fb_resnet=False):
        super(ResNet50, self).__init__(
            'resnet50', n_class, pretrained_model,
            mean, initialW, fb_resnet)


class ResNet101(ResNet):

    """ResNet-101 Network.

    Please consult the documentation for :class:`ResNet`.

    .. seealso::
        :class:`chainercv.links.model.resnet.ResNet`

    """

    def __init__(self, n_class=None, pretrained_model=None,
                 mean=None, initialW=None, fb_resnet=False):
        super(ResNet101, self).__init__(
            'resnet101', n_class, pretrained_model,
            mean, initialW, fb_resnet)


class ResNet152(ResNet):

    """ResNet-152 Network.

    Please consult the documentation for :class:`ResNet`.

    .. seealso::
        :class:`chainercv.links.model.resnet.ResNet`

    """

    def __init__(self, n_class=None, pretrained_model=None,
                 mean=None, initialW=None, fb_resnet=False):
        super(ResNet152, self).__init__(
            'resnet152', n_class, pretrained_model,
            mean, initialW, fb_resnet)


class HeNormal(chainer.initializer.Initializer):

    # fan_option is not supported in Chainer v3.

    def __init__(self, scale=1.0, dtype=None, fan_option='fan_in'):
        self.scale = scale
        self.fan_option = fan_option
        super(HeNormal, self).__init__(dtype)

    def __call__(self, array):
        if self.dtype is not None:
            assert array.dtype == self.dtype
        fan_in, fan_out = chainer.initializer.get_fans(array.shape)
        if self.fan_option == 'fan_in':
            s = self.scale * np.sqrt(2. / fan_in)
        elif self.fan_option == 'fan_out':
            s = self.scale * np.sqrt(2. / fan_out)
        else:
            raise ValueError(
                'fan_option should be either \'fan_in\' or \'fan_out\'.')
        initializers.Normal(s)(array)
