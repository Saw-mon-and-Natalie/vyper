import rlp
from eth_abi import encode_single
from hexbytes import HexBytes

from vyper.utils import checksum_encode, keccak256


# initcode used by create_minimal_proxy_to
def eip1167_initcode(_addr):
    addr = HexBytes(_addr)
    pre = HexBytes("0x602D3D8160093D39F3363d3d373d3d3d363d73")
    post = HexBytes("0x5af43d82803e903d91602b57fd5bf3")
    return HexBytes(pre + (addr + HexBytes(0) * (20 - len(addr))) + post)


# initcode used by CreateCopyOf
def vyper_initcode(runtime_bytecode):
    bytecode_len_hex = hex(len(runtime_bytecode))[2:].rjust(6, "0")
    return HexBytes("0x62" + bytecode_len_hex + "3d81600b3d39f3") + runtime_bytecode


def test_create_minimal_proxy_to_create(get_contract):
    code = """
main: address

@external
def test() -> address:
    self.main = create_minimal_proxy_to(self)
    return self.main
    """

    c = get_contract(code)

    address_bits = int(c.address, 16)
    nonce = 1
    rlp_encoded = rlp.encode([address_bits, nonce])
    expected_create_address = keccak256(rlp_encoded)[12:].rjust(20, b"\x00")
    assert c.test() == checksum_encode("0x" + expected_create_address.hex())


def test_create_minimal_proxy_to_call(get_contract, w3):
    code = """

interface SubContract:

    def hello() -> Bytes[100]: view


other: public(address)


@external
def test() -> address:
    self.other = create_minimal_proxy_to(self)
    return self.other


@external
def hello() -> Bytes[100]:
    return b"hello world!"


@external
def test2() -> Bytes[100]:
    return SubContract(self.other).hello()

    """

    c = get_contract(code)

    assert c.hello() == b"hello world!"
    c.test(transact={})
    assert c.test2() == b"hello world!"


def test_minimal_proxy_exception(w3, get_contract, assert_tx_failed):
    code = """

interface SubContract:

    def hello(a: uint256) -> Bytes[100]: view


other: public(address)


@external
def test() -> address:
    self.other = create_minimal_proxy_to(self)
    return self.other


@external
def hello(a: uint256) -> Bytes[100]:
    assert a > 0, "invaliddddd"
    return b"hello world!"


@external
def test2(a: uint256) -> Bytes[100]:
    return SubContract(self.other).hello(a)
    """

    c = get_contract(code)

    assert c.hello(1) == b"hello world!"
    c.test(transact={})
    assert c.test2(1) == b"hello world!"

    assert_tx_failed(lambda: c.test2(0))

    GAS_SENT = 30000
    tx_hash = c.test2(0, transact={"gas": GAS_SENT})

    receipt = w3.eth.get_transaction_receipt(tx_hash)

    assert receipt["status"] == 0
    assert receipt["gasUsed"] < GAS_SENT


def test_create_minimal_proxy_to_create2(
    get_contract, create2_address_of, keccak, assert_tx_failed
):
    code = """
main: address

@external
def test(_salt: bytes32) -> address:
    self.main = create_minimal_proxy_to(self, salt=_salt)
    return self.main
    """

    c = get_contract(code)

    salt = keccak(b"vyper")
    assert HexBytes(c.test(salt)) == create2_address_of(
        c.address, salt, eip1167_initcode(c.address)
    )

    c.test(salt, transact={})
    # revert on collision
    assert_tx_failed(lambda: c.test(salt, transact={}))


def test_create_from_factory(
    get_contract, deploy_factory_for, w3, keccak, create2_address_of, assert_tx_failed
):
    code = """
@external
def foo() -> uint256:
    return 123
    """

    deployer_code = """
created_address: public(address)

@external
def test(target: address):
    self.created_address = create_from_factory(target)

@external
def test2(target: address, salt: bytes32):
    self.created_address = create_from_factory(target, salt=salt)
    """

    # deploy a foo so we can compare its bytecode with factory deployed version
    foo_contract = get_contract(code)
    expected_runtime_code = w3.eth.get_code(foo_contract.address)

    f, FooContract = deploy_factory_for(code)

    d = get_contract(deployer_code)

    initcode = w3.eth.get_code(f.address)

    d.test(f.address, transact={})

    test = FooContract(d.created_address())
    assert w3.eth.get_code(test.address) == expected_runtime_code
    assert test.foo() == 123

    # extcodesize check
    assert_tx_failed(lambda: d.test("0x" + "00" * 20))

    # now same thing but with create2
    salt = keccak(b"vyper")
    d.test2(f.address, salt, transact={})

    test = FooContract(d.created_address())
    assert w3.eth.get_code(test.address) == expected_runtime_code
    assert test.foo() == 123

    assert HexBytes(test.address) == create2_address_of(d.address, salt, initcode)

    # can't collide addresses
    assert_tx_failed(lambda: d.test2(f.address, salt))


# test create_from_factory with args
def test_create_from_factory_args(
    get_contract, deploy_factory_for, w3, keccak, create2_address_of, assert_tx_failed
):
    code = """
FOO: immutable(String[128])

@external
def __init__(arg: String[128]):
    FOO = arg

@external
def foo() -> String[128]:
    return FOO
    """

    deployer_code = """
created_address: public(address)

@external
def test(target: address, arg: String[128]):
    self.created_address = create_from_factory(target, arg)

@external
def test2(target: address, arg: String[128], salt: bytes32):
    self.created_address = create_from_factory(target, arg, salt=salt)

@external
def should_fail(target: address, arg: String[129]):
    self.created_address = create_from_factory(target, arg)
    """
    FOO = "hello!"

    # deploy a foo so we can compare its bytecode with factory deployed version
    foo_contract = get_contract(code, FOO)
    expected_runtime_code = w3.eth.get_code(foo_contract.address)

    f, FooContract = deploy_factory_for(code)

    d = get_contract(deployer_code)

    initcode = w3.eth.get_code(f.address)

    d.test(f.address, FOO, transact={})

    test = FooContract(d.created_address())
    assert w3.eth.get_code(test.address) == expected_runtime_code
    assert test.foo() == FOO

    # extcodesize check
    assert_tx_failed(lambda: d.test("0x" + "00" * 20, FOO))

    # now same thing but with create2
    salt = keccak(b"vyper")
    d.test2(f.address, FOO, salt, transact={})

    test = FooContract(d.created_address())
    assert w3.eth.get_code(test.address) == expected_runtime_code
    assert test.foo() == FOO

    encoded_foo = encode_single("(string)", (FOO,))
    assert HexBytes(test.address) == create2_address_of(d.address, salt, initcode + encoded_foo)

    # can't collide addresses
    assert_tx_failed(lambda: d.test2(f.address, FOO, salt))

    # but creating a contract with different args is ok
    BAR = "bar"
    d.test2(f.address, BAR, salt, transact={})
    # just for kicks
    assert FooContract(d.created_address()).foo() == BAR

    # Foo constructor should fail
    assert_tx_failed(lambda: d.should_fail(f.address, b"\x01" * 129))


def test_create_copy_of(get_contract, w3, keccak, create2_address_of, assert_tx_failed):
    code = """
created_address: public(address)
@internal
def _create_copy_of(target: address):
    self.created_address = create_copy_of(target)

@internal
def _create_copy_of2(target: address, salt: bytes32):
    self.created_address = create_copy_of(target, salt=salt)

@external
def test(target: address) -> address:
    x: uint256 = 0
    self._create_copy_of(target)
    assert x == 0  # check memory not clobbered
    return self.created_address

@external
def test2(target: address, salt: bytes32) -> address:
    x: uint256 = 0
    self._create_copy_of2(target, salt)
    assert x == 0  # check memory not clobbered
    return self.created_address
    """

    c = get_contract(code)
    bytecode = w3.eth.get_code(c.address)

    c.test(c.address, transact={})
    test1 = c.created_address()
    assert w3.eth.get_code(test1) == bytecode

    # extcodesize check
    assert_tx_failed(lambda: c.test("0x" + "00" * 20))

    # test1 = c.test(b"\x01")
    # assert w3.eth.get_code(test1) == b"\x01"

    salt = keccak(b"vyper")
    c.test2(c.address, salt, transact={})
    test2 = c.created_address()
    assert w3.eth.get_code(test2) == bytecode

    assert HexBytes(test2) == create2_address_of(c.address, salt, vyper_initcode(bytecode))

    # can't create2 where contract already exists
    assert_tx_failed(lambda: c.test2(c.address, salt, transact={}))

    # test single byte contract
    # test2 = c.test2(b"\x01", salt)
    # assert HexBytes(test2) == create2_address_of(c.address, salt, vyper_initcode(b"\x01"))
    # assert_tx_failed(lambda: c.test2(bytecode, salt))
