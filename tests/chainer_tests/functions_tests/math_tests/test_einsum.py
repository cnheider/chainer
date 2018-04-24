import unittest

import numpy

import chainer
from chainer.backends import cuda
import chainer.functions as F
from chainer import gradient_check
from chainer import testing
from chainer.testing import attr
from chainer.utils.type_check import InvalidType

from chainer.functions.math.einsum import _diag_einsum


def _tuple_to_gpu(xs):
    return tuple(cuda.to_gpu(x) for x in xs)


@testing.parameterize(*testing.product_dict(
    [
        {'subscripts': 'ij,jk->ik', 'shapes': ((2, 3), (3, 4))},
        {'subscripts': ',ij->i', 'shapes': ((), (3, 4),)},
        {'subscripts': 'kj,ji->ik', 'shapes': ((2, 3), (3, 4))},
        {'subscripts': 'ij,jk,kl->il', 'shapes': ((5, 2), (2, 3), (3, 4))},
        {'subscripts': 'ij,ij->i', 'shapes': ((2, 3), (2, 3))},
        {'subscripts': 'ij,jk', 'shapes': ((2, 3), (3, 4))},
        {'subscripts': 'i->', 'shapes': ((3,),)},
        {'subscripts': 'ii', 'shapes': ((2, 2),)},
        {'subscripts': 'ii->i', 'shapes': ((2, 2),)},
        {'subscripts': 'j,j', 'shapes': ((3,), (3))},
        {'subscripts': 'j,ij', 'shapes': ((3,), (2, 3))},
        {'subscripts': 'j,iij', 'shapes': ((3,), (2, 2, 3))},
        {'subscripts': 'iij,kkj', 'shapes': ((2, 2, 3), (4, 4, 3))},
        {'subscripts': '...ij,...jk->...ik',
         'shapes': ((2, 1, 2, 3), (2, 1, 3, 4))},
        {'subscripts': 'i...j,jk...->k...i', 'shapes': ((4, 2, 3), (3, 5, 2))},
        {'subscripts': 'ii...,...jj', 'shapes': ((2, 2, 4), (4, 3, 3))},
        {'subscripts': '...i,i', 'shapes': ((2, 2, 3), (3,))},
        {'subscripts': 'i...,i->...i', 'shapes': ((3, 2, 2), (3,))},
    ],
    [
        # {'dtype': numpy.float16},  see numpy issue #10899
        {'dtype': numpy.float32},
        {'dtype': numpy.float64},
    ]
))
class TestEinSum(unittest.TestCase):

    def setUp(self):
        self.inputs = tuple([
            self._setup_tensor(-1, 1, shape, self.dtype)
            for shape in self.shapes
        ])
        self.forward_answer = numpy.einsum(self.subscripts, *self.inputs)
        self.g = self._setup_tensor(
            -1, 1, self.forward_answer.shape, self.dtype)
        self.gg_inputs = tuple([
            self._setup_tensor(-1, 1, shape, self.dtype)
            for shape in self.shapes
        ])
        self.op = lambda *xs: F.einsum(self.subscripts, *xs)

    def _setup_tensor(self, _min, _max, shape, dtype):
        return numpy.random.uniform(_min, _max, shape).astype(dtype)

    def check_forward(self, inputs_data, atol=1e-4, rtol=1e-5):
        out = self.op(*[chainer.Variable(x) for x in self.inputs])
        testing.assert_allclose(self.forward_answer, out.data, atol, rtol)

    def test_einsum_forward_cpu(self):
        if self.dtype == numpy.float16:
            self.check_forward(self.inputs, atol=1e-3, rtol=1e-3)
        else:
            self.check_forward(self.inputs)

    @attr.gpu
    def test_einsum_forward_gpu(self):
        inputs = _tuple_to_gpu(self.inputs)
        if self.dtype == numpy.float16:
            self.check_forward(inputs, atol=1e-3, rtol=1e-3)
        else:
            self.check_forward(inputs)

    def check_backward(self, inputs_data, output_grad, atol, rtol):
        gradient_check.check_backward(
            self.op, inputs_data, output_grad, atol=atol, rtol=rtol,
            dtype=numpy.float32)

    def test_einsum_backward_cpu(self):
        self.check_backward(self.inputs, self.g, atol=1e-2, rtol=5e-2)

    @attr.gpu
    def test_einsum_backward_gpu(self):
        self.check_backward(
            _tuple_to_gpu(self.inputs),
            cuda.to_gpu(self.g), atol=1e-2, rtol=5e-2)

    def check_double_backward(
            self, inputs_data, y_grad, inputs_grad_grad,
            atol, rtol):
        def nonlinear(*inputs):
            y = self.op(*inputs)
            return y * y
        gradient_check.check_double_backward(
            nonlinear, inputs_data, y_grad, inputs_grad_grad,
            atol=atol, rtol=rtol, dtype=numpy.float32)

    def test_einsum_double_backward_cpu(self):
        self.check_double_backward(
            self.inputs, self.g, self.gg_inputs,
            atol=1e-2, rtol=5e-2)

    @attr.gpu
    def test_einsum_double_backward_gpu(self):
        self.check_double_backward(
            _tuple_to_gpu(self.inputs), cuda.to_gpu(self.g),
            _tuple_to_gpu(self.gg_inputs), atol=1e-2, rtol=1e-2)


@testing.parameterize(
    # mismatch: 'i'
    {'subscripts': 'i,i', 'shapes': ((2,), (3,))},
    {'subscripts': 'i,i->i', 'shapes': ((2,), (3,))},
    {'subscripts': 'ii', 'shapes': ((2, 3),)},

    # mismatch: '...'
    {'subscripts': '...i,...i', 'shapes': ((2, 2), (3, 2))},
    {'subscripts': '...i,...j', 'shapes': ((2, 3), (3, 2))},
    {'subscripts': '...i,j...', 'shapes': ((2, 3), (2, 3))},
    {'subscripts': 'i...,j...', 'shapes': ((2, 3), (3, 2))},

    # F.einsum does not allow broadcasting
    {'subscripts': '...i,...i', 'shapes': ((2, 2), (1, 2))},
    {'subscripts': '...i,...i', 'shapes': ((2,), (1, 2))},
)
class TestEinSumInvalid(unittest.TestCase):

    def setUp(self):
        self.inputs = tuple([
            numpy.zeros(shape, numpy.float32)
            for shape in self.shapes
        ])

    def test_raise_invalid_type(self):
        with self.assertRaises(InvalidType):
            F.einsum(self.subscripts, *self.inputs)


@testing.parameterize(
    {'subscripts': 'i,i', 'shapes': ((2,), (2,), (2,))},
    {'subscripts': 'i,i', 'shapes': ((2,),)},
    {'subscripts': 'i,i->j', 'shapes': ((2,), (2,))},
    {'subscripts': 'i,i->...', 'shapes': ((2,), (2,))},
)
class TestEinSumParseError(unittest.TestCase):

    def setUp(self):
        self.inputs = tuple([
            numpy.zeros(shape, numpy.float32)
            for shape in self.shapes
        ])

    def test_raise_parse_error(self):
        with self.assertRaises(ValueError):
            F.einsum(self.subscripts, *self.inputs)


@testing.parameterize(*testing.product_dict(
    [
        {'subscripts': 'i->ij', 'i_shapes': ((3,),), 'o_shape': (3, 4)},
        {'subscripts': '->i', 'i_shapes': ((),), 'o_shape': (3,)},
        {'subscripts': ',i->ij', 'i_shapes': ((), (2,),), 'o_shape': (2, 3)},
        {'subscripts': ',ij->i', 'i_shapes': ((), (3, 4),), 'o_shape': (3,)},
    ],
    [
        # {'dtype': numpy.float16},
        {'dtype': numpy.float32},
        {'dtype': numpy.float64},
    ]
))
class TestDiagEinSum(unittest.TestCase):

    def setUp(self):
        self.inputs = [
            self._setup_tensor(-1, 1, shape, self.dtype)
            for shape in self.i_shapes
        ]
        # self.forward_answer = numpy.einsum(self.subscripts, *self.inputs)
        self.g = self._setup_tensor(-1, 1, self.o_shape, self.dtype)
        self.gg_inputs = [
            self._setup_tensor(-1, 1, shape, self.dtype)
            for shape in self.i_shapes
        ]
        i_sub, o_sub = self.subscripts.split('->')
        self.op = lambda *xs: _diag_einsum(
            i_sub, o_sub, *xs, output_shape=self.o_shape)

    def _setup_tensor(self, _min, _max, shape, dtype):
        return numpy.random.uniform(_min, _max, shape).astype(dtype)

    """
    def check_forward(self, inputs_data, atol=1e-4, rtol=1e-5):
        out = self.op(*[chainer.Variable(x) for x in self.inputs])
        testing.assert_allclose(self.forward_answer, out.data, atol, rtol)

    def test_einsum_forward_cpu(self):
        if self.dtype == numpy.float16:
            self.check_forward(self.inputs, atol=1e-3, rtol=1e-3)
        else:
            self.check_forward(self.inputs)

    @attr.gpu
    def test_einsum_forward_gpu(self):
        inputs = [cuda.to_gpu(x) for x in self.inputs]
        if self.dtype == numpy.float16:
            self.check_forward(self.inputs, atol=1e-3, rtol=1e-3)
        else:
            self.check_forward(self.inputs)
    """

    def check_backward(self, inputs_data, output_grad, atol, rtol):
        gradient_check.check_backward(
            self.op, inputs_data, output_grad, atol=atol, rtol=rtol,
            dtype=numpy.float32)

    def test_einsum_backward_cpu(self):
        self.check_backward(self.inputs, self.g, atol=1e-2, rtol=5e-2)

    @attr.gpu
    def test_einsum_backward_gpu(self):
        self.check_backward(
            _tuple_to_gpu(self.inputs),
            cuda.to_gpu(self.g), atol=1e-2, rtol=5e-2)

    def check_double_backward(
            self, inputs_data, y_grad, inputs_grad_grad,
            atol, rtol):
        def nonlinear(*inputs):
            y = self.op(*inputs)
            return y * y
        gradient_check.check_double_backward(
            nonlinear, inputs_data, y_grad, inputs_grad_grad,
            atol=atol, rtol=rtol, dtype=numpy.float32)

    def test_einsum_double_backward_cpu(self):
        self.check_double_backward(
            self.inputs, self.g, self.gg_inputs,
            atol=1e-2, rtol=5e-2)

    @attr.gpu
    def test_einsum_double_backward_gpu(self):
        self.check_double_backward(
            _tuple_to_gpu(self.inputs), cuda.to_gpu(self.g),
            _tuple_to_gpu(self.gg_inputs), atol=1e-2, rtol=1e-2)


testing.run_module(__name__, __file__)
