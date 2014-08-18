"""
Operators for enforcing linear combination boundary conditions in nodal FEM
setting.
"""
import numpy as nm
import scipy.sparse as sp

from sfepy.base.base import output, assert_, find_subclasses, Container, Struct
from sfepy.discrete.common.dof_info import DofInfo, expand_nodes_to_equations
from sfepy.discrete.fem.utils import (compute_nodal_normals,
                                      compute_nodal_edge_dirs)
from sfepy.discrete.conditions import get_condition_value

class LCBCOperator(Struct):
    """
    Base class for LCBC operators.
    """

    def __init__(self, name, regions, dof_names, dof_map_fun, variables,
                 functions=None):
        Struct.__init__(self, name=name, regions=regions, dof_names=dof_names)

        if dof_map_fun is not None:
            self.dof_map_fun = get_condition_value(dof_map_fun, functions,
                                                   'LCBC', 'dof_map_fun')
        self._setup_dof_names(variables)

    def _setup_dof_names(self, variables):
        self.var_names = [dd[0].split('.')[0] for dd in self.dof_names]
        self.all_dof_names = [variables[ii].dofs for ii in self.var_names]

    def setup(self):
        pass

class MRLCBCOperator(LCBCOperator):
    """
    Base class for model-reduction type LCBC operators.
    """

    def __init__(self, name, regions, dof_names, dof_map_fun, variables,
                 functions=None):
        Struct.__init__(self, name=name, region=regions[0],
                        dof_names=dof_names[0])

        self._setup_dof_names(variables)

        self.eq_map = variables[self.var_name].eq_map
        self.field = variables[self.var_name].field
        self.mdofs = self.field.get_dofs_in_region(self.region, merge=True)

        self.n_sdof = 0

    def _setup_dof_names(self, variables):
        self.var_name = self.dof_names[0].split('.')[0]
        self.var_names = [self.var_name, None]
        self.all_dof_names = variables[self.var_name].dofs

    def setup(self):
        eq = self.eq_map.eq
        meq = expand_nodes_to_equations(self.mdofs, self.dof_names,
                                         self.all_dof_names)
        ameq = eq[meq]
        assert_(nm.all(ameq >= 0))

        if self.eq_map.n_epbc:
            self.treat_pbcs(meq, self.eq_map.master)

        self.ameq = ameq

    def treat_pbcs(self, dofs, master):
        """
        Treat dofs with periodic BC.
        """
        master = nm.intersect1d(dofs, master)
        if len(master) == 0: return

        # Remove rows with master DOFs.
        remove = nm.searchsorted(nm.sort(dofs), master)
        keep = nm.setdiff1d(nm.arange(len(dofs)), remove)

        mtx = self.mtx[keep]

        # Remove empty columns, update new DOF count.
        mtx = mtx.tocsc()
        indptr = nm.unique(mtx.indptr)
        self.mtx = sp.csc_matrix((mtx.data, mtx.indices, indptr),
                                 shape=(mtx.shape[0], indptr.shape[0] - 1))
        self.n_dof = self.mtx.shape[1]

class RigidOperator(LCBCOperator):
    """
    Transformation matrix operator for rigid LCBCs.
    """
    kind = 'rigid'

    def __init__(self, name, nodes, field, dof_names, all_dof_names):
        Struct.__init__(self, name=name, nodes=nodes, dof_names=dof_names)

        coors = field.get_coor(nodes)
        n_nod, dim = coors.shape

        mtx_e = nm.tile(nm.eye(dim, dtype=nm.float64), (n_nod, 1))

        if dim == 2:
            mtx_r = nm.empty((dim * n_nod, 1), dtype=nm.float64)
            mtx_r[0::dim,0] = -coors[:,1]
            mtx_r[1::dim,0] = coors[:,0]
            n_rigid_dof = 3

        elif dim == 3:
            mtx_r = nm.zeros((dim * n_nod, dim), dtype=nm.float64)
            mtx_r[0::dim,1] = coors[:,2]
            mtx_r[0::dim,2] = -coors[:,1]
            mtx_r[1::dim,0] = -coors[:,2]
            mtx_r[1::dim,2] = coors[:,0]
            mtx_r[2::dim,0] = coors[:,1]
            mtx_r[2::dim,1] = -coors[:,0]
            n_rigid_dof = 6

        else:
            msg = 'dimension in [2, 3]: %d' % dim
            raise ValueError(msg)

        self.n_dof = n_rigid_dof
        self.mtx = nm.hstack((mtx_r, mtx_e))

        # Strip unconstrained dofs.
        aux = dim * nm.arange(n_nod)
        indx = [aux + all_dof_names.index(dof) for dof in dof_names]
        indx = nm.array(indx).T.ravel()

        self.mtx = self.mtx[indx]

def _save_vectors(filename, vectors, region, mesh, data_name):
    """
    Save vectors defined in region nodes as vector data in mesh vertices.
    """
    nv = nm.zeros_like(mesh.coors)
    nmax = region.vertices.shape[0]
    nv[region.vertices] = vectors[:nmax]

    out = {data_name : Struct(name='output_data', mode='vertex', data=nv)}
    mesh.write(filename, out=out, io='auto')

class NoPenetrationOperator(LCBCOperator):
    """
    Transformation matrix operator for no-penetration LCBCs.
    """
    kind = 'no_penetration'

    def __init__(self, name, nodes, region, field, dof_names, filename=None):
        Struct.__init__(self, name=name, nodes=nodes, dof_names=dof_names)

        dim = region.dim
        assert_(len(dof_names) == dim)

        normals = compute_nodal_normals(nodes, region, field)

        if filename is not None:
            _save_vectors(filename, normals, region, field.domain.mesh, 'n')

        ii = nm.abs(normals).argmax(1)
        n_nod, dim = normals.shape

        irs = set(range(dim))

        data = []
        rows = []
        cols = []
        for idim in xrange(dim):
            ic = nm.where(ii == idim)[0]
            if len(ic) == 0: continue

            ir = list(irs.difference([idim]))
            nn = nm.empty((len(ic), dim - 1), dtype=nm.float64)
            for ik, il in enumerate(ir):
                nn[:,ik] = - normals[ic,il] / normals[ic,idim]

            irn = dim * ic + idim
            ics = [(dim - 1) * ic + ik for ik in xrange(dim - 1)]
            for ik in xrange(dim - 1):
                rows.append(irn)
                cols.append(ics[ik])
                data.append(nn[:,ik])

            ones = nm.ones((nn.shape[0],), dtype=nm.float64)
            for ik, il in enumerate(ir):
                rows.append(dim * ic + il)
                cols.append(ics[ik])
                data.append(ones)

        rows = nm.concatenate(rows)
        cols = nm.concatenate(cols)
        data = nm.concatenate(data)

        n_np_dof = n_nod * (dim - 1)
        mtx = sp.coo_matrix((data, (rows, cols)), shape=(n_nod * dim, n_np_dof))

        self.n_dof = n_np_dof
        self.mtx = mtx.tocsr()

class NormalDirectionOperator(LCBCOperator):
    """
    Transformation matrix operator for normal direction LCBCs.

    The substitution (in 3D) is:

    .. math::
        [u_1, u_2, u_3]^T = [n_1, n_2, n_3]^T w

    The new DOF is :math:`w`.
    """
    kind = 'normal_direction'

    def __init__(self, name, nodes, region, field, dof_names, filename=None):
        Struct.__init__(self, name=name, nodes=nodes, dof_names=dof_names)

        dim = region.dim
        assert_(len(dof_names) == dim)

        vectors = self.get_vectors(nodes, region, field, filename=filename)

        n_nod, dim = vectors.shape

        data = vectors.ravel()
        rows = nm.arange(data.shape[0])
        cols = nm.repeat(nm.arange(n_nod), dim)

        mtx = sp.coo_matrix((data, (rows, cols)), shape=(n_nod * dim, n_nod))

        self.n_dof = n_nod
        self.mtx = mtx.tocsr()

    def get_vectors(self, nodes, region, field, filename=None):
        normals = compute_nodal_normals(nodes, region, field)

        if filename is not None:
            _save_vectors(filename, normals, region, field.domain.mesh, 'n')

        return normals

class EdgeDirectionOperator(NormalDirectionOperator):
    """
    Transformation matrix operator for edges direction LCBCs.

    The substitution (in 3D) is:

    .. math::
        [u_1, u_2, u_3]^T = [d_1, d_2, d_3]^T w,

    where :math:`\ul{d}` is an edge direction vector averaged into a node. The
    new DOF is :math:`w`.
    """
    kind = 'edge_direction'

    def get_vectors(self, nodes, region, field, filename=None):
        edirs = compute_nodal_edge_dirs(nodes, region, field)

        if filename is not None:
            _save_vectors(filename, edirs, region, field.domain.mesh, 'e')

        return edirs

class IntegralMeanValueOperator(MRLCBCOperator):
    """
    Transformation matrix operator for integral mean value LCBCs.
    All node DOFs are sumed to the new one.
    """
    kind = 'integral_mean_value'

    def __init__(self, name, regions, dof_names, dof_map_fun,
                 variables, ts=None, functions=None):
        MRLCBCOperator.__init__(self, name, regions, dof_names, dof_map_fun,
                                variables, functions=functions)

        dpn = len(self.dof_names)
        n_nod = self.mdofs.shape[0]

        data = nm.ones((n_nod * dpn,))
        rows = nm.arange(data.shape[0])
        cols = nm.zeros((data.shape[0],))

        mtx = sp.coo_matrix((data, (rows, cols)), shape=(n_nod * dpn, dpn))

        self.n_mdof = n_nod * dpn
        self.n_new_dof = dpn
        self.mtx = mtx.tocsr()

class ShiftedPeriodicOperator(LCBCOperator):
    """
    Transformation matrix operator shifted periodic boundary conditions.
    """
    kind = 'shifted_periodic'

    def __init__(self, name, regions, dof_names, dof_map_fun, shift_fun,
                 variables, ts, functions):
        LCBCOperator.__init__(self, name, regions, dof_names, dof_map_fun,
                              variables, functions=functions)

        self.shift_fun = get_condition_value(shift_fun, functions,
                                             'LCBC', 'shift')

        mvar = variables[self.var_names[0]]
        svar = variables[self.var_names[1]]

        mfield = mvar.field
        sfield = svar.field

        nmaster = mfield.get_dofs_in_region(regions[0], merge=True)
        nslave = sfield.get_dofs_in_region(regions[1], merge=True)

        if nmaster.shape != nslave.shape:
            msg = 'shifted EPBC node list lengths do not match!\n(%s,\n %s)' %\
                  (nmaster, nslave)
            raise ValueError(msg)

        mcoor = mfield.get_coor(nmaster)
        scoor = sfield.get_coor(nslave)

        i1, i2 = self.dof_map_fun(mcoor, scoor)
        self.mdofs = expand_nodes_to_equations(nmaster[i1], dof_names[0],
                                               self.all_dof_names[0])
        self.sdofs = expand_nodes_to_equations(nslave[i2], dof_names[1],
                                               self.all_dof_names[1])

        self.shift = self.shift_fun(ts, scoor[i2], regions[1])

        meq = mvar.eq_map.eq[self.mdofs]
        seq = svar.eq_map.eq[self.sdofs]

        # Ignore DOFs with EBCs or EPBCs.
        mia = nm.where(meq >= 0)[0]
        sia = nm.where(seq >= 0)[0]

        ia = nm.intersect1d(mia, sia)

        meq = meq[ia]
        seq = seq[ia]

        num = len(ia)

        ones = nm.ones(num, dtype=nm.float64)
        n_dofs = [variables.adi.n_dof[name] for name in self.var_names]
        mtx = sp.coo_matrix((ones, (meq, seq)), shape=n_dofs)

        self.mtx = mtx.tocsr()

        self.rhs = self.shift.ravel()[ia]

        self.ameq = meq
        self.aseq = seq

        self.n_mdof = len(nm.unique(meq))
        self.n_sdof = len(nm.unique(seq))
        self.n_new_dof = 0

class LCBCOperators(Container):
    """
    Container holding instances of LCBCOperator subclasses for a single
    variable.
    """

    def __init__(self, name, variables, functions=None):
        """
        Parameters
        ----------
        name : str
            The object name.
        variables : Variables instance
            The variables to be constrained.
        functions : Functions instance, optional
            The user functions for DOF matching and other LCBC
            subclass-specific tasks.
        """
        Container.__init__(self, name=name, variables=variables,
                           functions=functions)

        self.markers = []
        self.n_new_dof = []
        self.n_op = 0
        self.ics = None

        self.classes = find_subclasses(globals(), [LCBCOperator],
                                       omit_unnamed=True,
                                       name_attr='kind')

    def add_from_bc(self, bc, ts):
        """
        Create a new LCBC operator described by `bc`, and add it to the
        container.

        Parameters
        ----------
        bc : LinearCombinationBC instance
            The LCBC condition description.
        ts : TimeStepper instance
            The time stepper.
        """
        try:
            cls = self.classes[bc.kind]

        except KeyError:
            raise ValueError('unknown LCBC kind! (%s)' % bc.kind)

        args = bc.arguments + (self.variables, ts, self.functions)

        op = cls('%d_%s' % (len(self), bc.kind), bc.regions, bc.dofs,
                 bc.dof_map_fun, *args)

        op.setup()

        self.append(op)

    def append(self, op):
        Container.append(self, op)

        self.markers.append(self.n_op + 1)
        self.n_new_dof.append(op.n_new_dof)

        self.n_op = len(self)

    def finalize(self):
        """
        Call this after all LCBCs of the variable have been added.

        Initializes the global column indices and DOF counts.
        """
        keys = self.variables.adi.var_names
        self.n_master = {}.fromkeys(keys, 0)
        self.n_slave = {}.fromkeys(keys, 0)
        self.n_new = {}.fromkeys(keys, 0)
        ics = {}
        for ii, op in enumerate(self):
            self.n_master[op.var_names[0]] += op.n_mdof

            if op.var_names[1] is not None:
                self.n_slave[op.var_names[1]] += op.n_sdof

            self.n_new[op.var_names[0]] += op.n_new_dof
            ics.setdefault(op.var_names[0], []).append((ii, op.n_new_dof))

        self.ics = {}
        for key, val in ics.iteritems():
            iis, ics = zip(*val)
            self.ics[key] = (iis, nm.cumsum(nm.r_[0, ics]))

        self.n_free = {}
        self.n_active = {}
        n_dof = self.variables.adi.n_dof
        for key in self.n_master.iterkeys():
            self.n_free[key] = n_dof[key] - self.n_master[key]
            self.n_active[key] = self.n_free[key] + self.n_new[key]

        def _dict_to_di(name, dd):
            di = DofInfo(name)
            for key, val in dd.iteritems():
                di.append_raw(key, val)
            return di

        self.lcdi = _dict_to_di('lcbc_active_state_dof_info', self.n_active)
        self.fdi = _dict_to_di('free_dof_info', self.n_free)
        self.ndi = _dict_to_di('new_dof_info', self.n_new)

    def make_global_operator(self, adi, new_only=False):
        """
        Assemble all LCBC operators into a single matrix.

        Parameters
        ----------
        adi : DofInfo
            The active DOF information.
        new_only : bool
            If True, the operator columns will contain only new DOFs.

        Returns
        -------
        mtx_lc : csr_matrix
            The global LCBC operator in the form of a CSR matrix.
        rhs_lc : array
            The right-hand side for non-homogeneous LCBCs.
        lcdi : DofInfo
            The global active LCBC-constrained DOF information.
        """
        self.finalize()

        if len(self) == 0: return (None,) * 3

        n_dof = self.variables.adi.ptr[-1]
        n_constrained = nm.sum([val for val in self.n_master.itervalues()])
        n_dof_free = nm.sum([val for val in self.n_free.itervalues()])
        n_dof_new = nm.sum([val for val in self.n_new.itervalues()])
        n_dof_active = nm.sum([val for val in self.n_active.itervalues()])

        output('dofs: total %d, free %d, constrained %d, new %d'\
               % (n_dof, n_dof_free, n_constrained, n_dof_new))
        output(' -> active %d' % (n_dof_active))

        adi = self.variables.adi
        lcdi, ndi, fdi = self.lcdi, self.ndi, self.fdi

        rows = []
        cols = []
        data = []

        lcbc_mask = nm.ones(n_dof, dtype=nm.bool)
        for ii, op in enumerate(self):
            rvar_name = op.var_names[0]
            roff = adi.indx[rvar_name].start

            irs = roff + op.ameq
            lcbc_mask[irs] = False

        vec_lc = nm.zeros(n_dof, dtype=nm.float64)

        for ii, op in enumerate(self):
            rvar_name = op.var_names[0]
            roff = adi.indx[rvar_name].start

            irs = roff + op.ameq

            cvar_name = op.var_names[1]
            if cvar_name is None:
                if new_only:
                    coff = ndi.indx[rvar_name].start

                else:
                    coff = lcdi.indx[rvar_name].start + fdi.n_dof[rvar_name]

                iis, icols = self.ics[rvar_name]
                ici = nm.searchsorted(iis, ii)
                ics = nm.arange(coff + icols[ici], coff + icols[ici+1])
                if isinstance(op.mtx, sp.spmatrix):
                    lr, lc, lv = sp.find(op.mtx)
                    rows.append(irs[lr])
                    cols.append(ics[lc])
                    data.append(lv)

                else:
                    _irs, _ics = nm.meshgrid(irs, ics)
                    rows.append(_irs.ravel())
                    cols.append(_ics.ravel())
                    data.append(op.mtx.T.ravel())

            else:
                coff = lcdi.indx[cvar_name].start

                lr, lc, lv = sp.find(op.mtx)
                ii1 = nm.where(lcbc_mask[adi.indx[cvar_name]])[0]
                ii2 = nm.searchsorted(ii1, lc)

                rows.append(roff + lr)
                cols.append(coff + ii2)
                data.append(lv)

                vec_lc[irs] += op.rhs

        rows = nm.concatenate(rows)
        cols = nm.concatenate(cols)
        data = nm.concatenate(data)

        if new_only:
            mtx_lc = sp.coo_matrix((data, (rows, cols)),
                                   shape=(n_dof, n_dof_new))

        else:
            mtx_lc = sp.coo_matrix((data, (rows, cols)),
                                   shape=(n_dof, n_dof_active))

            ir = nm.where(lcbc_mask)[0]
            ic = nm.empty((n_dof_free,), dtype=nm.int32)
            for var_name in adi.var_names:
                ii = nm.arange(fdi.n_dof[var_name], dtype=nm.int32)
                ic[fdi.indx[var_name]] = lcdi.indx[var_name].start + ii

            mtx_lc2 = sp.coo_matrix((nm.ones((ir.shape[0],)), (ir, ic)),
                                    shape=(n_dof, n_dof_active),
                                    dtype=nm.float64)

            mtx_lc = mtx_lc + mtx_lc2

        mtx_lc = mtx_lc.tocsr()

        return mtx_lc, vec_lc, lcdi
