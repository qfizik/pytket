# Copyright 2018 Cambridge Quantum Computing
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""Methods to allow conversion between Qiskit and pytket circuit graphs
"""

import qiskit
from qiskit import QuantumRegister, ClassicalRegister, QuantumCircuit
from qiskit.circuit import Instruction
from qiskit.dagcircuit import DAGCircuit
from qiskit.mapper import CouplingMap
from qiskit.converters import circuit_to_dag

import sympy
from sympy import N, pi

from pytket._circuit import OpType, Op, Circuit
from pytket._routing import DirectedGraph, Architecture

import time
from typing import List

import logging
logger = logging.getLogger(__name__)

DEBUG=False
"""Turn on debugging output to console.
"""

BOX_UNKNOWN=True
"""If True then tket will interpret any unknown ops as OpType.Box and
   store the name of the op in the desc field of the Box.
"""

DROP_CONDS=False
"""If True, tket will silently discard any classical conditions attached to 
   any op (If DEBUG is True then a warning message will be emitted.)  If False,
   an exception will be raised for any op with classical conditions.
"""



_name_index=0
def _fresh_name(prefix="tk_c") :
    global _name_index
    _name_index+=1
    return prefix + str(_name_index)

def dagcircuit_to_tk(dag:DAGCircuit, _BOX_UNKNOWN:bool=BOX_UNKNOWN, _DROP_CONDS:bool=DROP_CONDS) -> Circuit :
    """Converts a qiskit.DAGCircuit into a t|ket> Circuit
    Not all ops are supported.  Classical registers are supported only as 
    the output of measurements.  Does not attempt to preserve the structure 
    of the quantum registers; instead creates one big quantum register.

    :param dag: a circuit to be converted

    :return: The converted circuit
    """
    qs = dag.get_qubits()
    qnames = ["%s[%d]" % (r.name, i) for r, i in qs]
    g = dag.multi_graph
    circ = Circuit()
    if DEBUG :
        print("new graph w " + str(len(qs)) + " qubits")
        print(str(qs))

    # process vertices
    tk_vs = [ None for _ in range(dag.node_counter + 1) ]    
    for n in g.nodes :
        node = g.nodes[n]
        if DEBUG : 
            print(str(n) + " " + str(node["type"])+ " " + str(node["name"]))
        if ((node["type"]=="in" or node["type"]=="out") and not node["name"] in qnames) :
            # don't create vertices for in/outs of classical registers
            if DEBUG:
                print("Dropping node " + str(n))
            continue
        else :
            tk_vs[n] = circ._add_vertex(_node_converter(circ, node, _BOX_UNKNOWN=_BOX_UNKNOWN, _DROP_CONDS=_DROP_CONDS))
            if DEBUG:
                print("qiskit vertex " + str(n) + " is t|ket> vertex " +str(tk_vs[n]))
    # process edges
    for e in g.edges(data=True) :
        wire = e[2]["wire"]
        if wire in qs : # ignore classical wires
            src_port = _get_port_for_edge(g.node[e[0]], wire)
            tgt_port = _get_port_for_edge(g.node[e[1]], wire)
            if DEBUG : 
                print(_make_edge_str(tk_vs[e[0]],src_port,tk_vs[e[1]],tgt_port))
            circ._add_edge(tk_vs[e[0]],src_port,tk_vs[e[1]],tgt_port)                

    return circ

def _get_port_for_edge(node, qbit) :
    if node['type'] != 'op' :
        return 0
    ports = node['qargs']
    if len(ports) <= 1 :
        return 0
    for i in range(len(ports)) :
        if ports[i] == qbit :
            return i
    raise RuntimeError("Can't find port " + str(qbit) + " on op " + str(node))


### pretty priting for edges
def _make_edge_str(src,src_port,tgt,tgt_port):
    return str(src) + "." + str(src_port) + " --> " + str(tgt) + "." + str(tgt_port)
    

def _node_converter(circuit, node, _BOX_UNKNOWN=BOX_UNKNOWN, _DROP_CONDS=DROP_CONDS) :
    if node["type"]=="in" :
        return _const_ops["in"]
    elif node["type"]=="out":
        return _const_ops["out"]
    elif node["type"]=="op" :
        if node["name"] in _known_ops :
            ### TODO : cannot handle non-trivial conditions
            if 'condition' in node and node['condition'] :
                if _DROP_CONDS :
                    if DEBUG :
                        print("WARNING : dropped the condition from op " + str(node))
                    else :
                        pass
                else :
                    raise NotImplementedError("Conditional ops are not supported : " + str(node))
            ### TODO : support for classical wires to be added
            ### Classical regsisters are inferred from the measurements only
            if node['name'] == "measure" :
                return _make_measure_op(circuit, node)
            elif node['name'] == "symrz" :
                return circuit._get_op(OpType.SymbolicRz,1,1,node['op'].desc)
            else :
                if len(node['cargs']) > 0 :
                    raise NotImplementedError("Classical arguments not supported for op type " + node['name'])
                nparams = len(node["op"].param)
                if nparams == 0 :
                    return _const_ops[node["name"]]
                elif nparams == 1 :
                    return _make_one_param_op(circuit, node)
                else:
                    return _make_multi_param_op(circuit, node)
        else:
            if _BOX_UNKNOWN :
                return _make_box_node(circuit, node)
            else :
                raise NotImplementedError("Unknown Op type : " + node['name'])
    else :
        raise NotImplementedError("Unknown node type : " + node["type"])        

_known_ops = {
    "in"  : OpType.Input,
    "out" : OpType.Output,
    "id"  : OpType.noop,
    "x"   : OpType.X,
    "y"   : OpType.Y,
    "z"   : OpType.Z,
    "s"   : OpType.S,
    "sdg" : OpType.Sdg,    
    "t"   : OpType.T,
    "tdg" : OpType.Tdg,    
    "v"   : OpType.V,
    "vdg" : OpType.Vdg,
    "h"   : OpType.H,    
    "rx"  : OpType.Rx,
    "ry"  : OpType.Ry,        
    "rz"  : OpType.Rz,
    "u1"  : OpType.U1,
    "u2"  : OpType.U2,
    "u3"  : OpType.U3,        
    "cx"  : OpType.CX,
    "cy"  : OpType.CY,        
    "cz"  : OpType.CZ,
    "ch"  : OpType.CH,        
    "ccx" : OpType.CCX,
    "crz" : OpType.CRz,
    "cu1" : OpType.CU1,
    "cu3" : OpType.CU3,    
    "phase" : OpType.PhaseGadget,
    "symrz" : OpType.SymbolicRz,
    "measure" : OpType.Measure 
}

_known_ops_rev = {v : k for k, v in _known_ops.items() }

_const_ops = {
    "in"  : Op.Input(),
    "out" : Op.Output(),
    "id"  : Op.noop(),
    "x"   : Op.X(),
    "y"   : Op.Y(),
    "z"   : Op.Z(),
    "s"   : Op.S(),
    "sdg" : Op.Sdg(),    
    "t"   : Op.T(),
    "tdg" : Op.Tdg(),    
    "h"   : Op.H(),    
    "cx"  : Op.CX(),
    "cy"  : Op.CY(),        
    "cz"  : Op.CZ(),
    "ch"  : Op.CH(),        
    "ccx" : Op.CCX()
}

def _normalise_param_in(p) :
    # need to strip factors of pi from float valued params
    ## sympy is kind of a pain
    try :
        val = p.evalf() # if this works we have a sympy expression
        return val / pi.evalf()
    except AttributeError :
        if type(p) == float or type(p) == sympy.core.numbers:
            return p / pi.evalf()
        else :
            return p

def _make_one_param_op (circuit, node) :
    arity = len(node['qargs'])
    param = _normalise_param_in(node['op'].param[0])
    return circuit._get_op(_known_ops[node['name']],arity,arity,param)

def _make_multi_param_op (circuit, node) :
    arity = len(node['qargs'])
    params = list(map(_normalise_param_in, node['op'].param))
    return circuit._get_op(_known_ops[node['name']],arity,arity,params)

def _make_measure_op (circuit, node ) :
    n = len(node['qargs'])
    if DEBUG : 
        print("made " + str(node['cargs']))
    return circuit._get_op(OpType.Measure,n,n,str(node['cargs']))
    

def _make_box_node(circuit, node) :
    if DEBUG : 
        print("WARNING : Don't know how to handle node : " + str(node))
        print("WARNING : Making a box instead")     
    n = len(node['qargs'])
    return circuit._get_op(OpType.Box,n,n,node['name'])


def _read_qasm_file(filename) :
    """Read a qasm file and return the corresponding tket Circuit.
    Not all QASM constructs are supported."""
    qasm = QuantumCircuit.from_qasm_file(filename)
    return dagcircuit_to_tk(circuit_to_dag(qasm))

##### ===== Conversion tk --> DAGCircuit #######

def tk_to_dagcircuit(circ:Circuit,_qreg_name:str="q") -> DAGCircuit :
    """
       Convert a t|ket> Circuit to a qiskit.DAGCircuit. Requires
       that the Circuit only conatins OpTypes from the qelib set.
    
    :param circ: A circuit to be converted

    :return: The converted circuit
    """
    dc = DAGCircuit()
    qreg = QuantumRegister(circ.n_qubits(), name=_qreg_name)
    dc.add_qreg(qreg)
    grid = circ._get_routing_grid()
    slices = _grid_to_slices(grid)
    qubits = _grid_to_qubits(grid, qreg)
    for s in slices :
        for v in s:
            o = circ._unsigned_to_op(v)
            qargs = [ qubits[(v,i)] for i in range(o.get_n_inputs()) ]
            name, cargs, params = _translate_ops(circ,v)
            if cargs :
                _extend_cregs(dc,cargs)
            if name :
                dc.add_basis_element(name,o.get_n_inputs(),number_classical=len(cargs),number_parameters=len(params))
                ins = Instruction(name, list(map(_normalise_param_out, params)), qargs, cargs)
                dc.apply_operation_back(ins ,qargs=qargs,
                                        cargs=cargs)
    return dc 

def _grid_to_slices(grid):
    slices = []
    for s in grid:
        news = set()
        for pair in s:
            if pair[0]>-1:
                news.add(pair[0])
        slices.append(news)
    return slices
    

def _grid_to_qubits(grid,qreg_name="q") :
    lut = {}
    for i in range(len(grid)) :
        for j in range(len(grid[i])) :
            if grid[i][j][0]:
                lut[grid[i][j]] = (qreg_name,j)
    return lut

def _paths_to_qubits_bis(paths,qreg_name="q") :
    lut = {}
    for i in range(len(paths)) :
        for x in paths[i] :
            lut[x] = qreg_name + "[" + str(i) + "]"
    return lut


def _normalise_param_out(p) :
    # tk float params are implicitly fractions of pi
    # Convert these to sympy numbers for QISKIT
    # other params just preserved
    if type(p) == float :
        return N(p*pi)
    else:
        return p
    
def _translate_ops(circ,v) :
    o = circ._unsigned_to_op(v)
    if o.get_type() == OpType.Input or o.get_type() == OpType.Output :
        # don't need to create vertices for input and output
        return (None, None, None) 

    if o.get_type() == OpType.Box :
        name = o.get_desc()
    elif o.get_type() in _known_ops_rev :
        name = _known_ops_rev[o.get_type()]
    else :
        raise NotImplementedError("OpType " + o.get_name() + " not supported")

    if o.get_type() == OpType.Measure :
        # this is a temporary hack - to be removed once tket supports classical wires.
        cargs = eval(o.get_desc())
        if not cargs :
            creg = _fresh_name()
            cargs = [(creg,i) for i in range(o.get_n_outputs())]
    else : 
        cargs = []
    if DEBUG and name == "measure" :
        print(cargs)
    return (name, cargs, o.get_params())


def _extend_cregs(dc,cargs) :
    for reg, idx in dict(sorted(cargs)).items() :
        if reg.name not in dc.cregs:
            dc.add_creg(reg)
        elif idx >= dc.cregs[reg.name].size :
            old_size = dc.cregs[reg].size
            dc.cregs[reg].size = idx+1
            for j in range(idx+1 - old_size) : 
                dc._add_wire((reg,j+old_size),True)


def coupling2directed(coupling_map:List[List[int]]) -> DirectedGraph:
    """
       Produces a tket Architecture corresponding to a (directed) coupling map,
       stating the pairs of qubits between which two-qubit interactions
       (e.g. CXs) can be applied.

    :param coupling_map: Pairs of indices where each pair [control, target]
       permits the use of CXs between them
    
    :return: The tket architecture capturing the behaviour of the coupling map
    """
    coupling = CouplingMap(couplinglist=coupling_map)
    return DirectedGraph(coupling_map,coupling.size())
    