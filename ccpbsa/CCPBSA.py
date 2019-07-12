#!/Applications/PyMOL.app/Contents/bin/python3
"""Code for CC/PBSA, a fast tool for estimating folding free energy
differences.
"""
import glob
import os
import shutil
import subprocess
import numpy as np
import pandas as pd
import pymol
pymol.finish_launching(['pymol', '-qc'])
cmd = pymol.cmd

ERRORSTR = """Bad energy values from unminimized structures.
Change .minimized to True or run the minimize method first.
"""


def int_in_str(lst):
    converted = ''.join([item for item in lst if item.isdigit()])
    return int(converted)

def log(fname, proc_obj):
    """Used to log some of GROMACS output
    """
    log_file = open(fname, 'a')

    if hasattr(proc_obj.stdout, 'decode'):
        log_file.write(proc_obj.stdout.decode('utf-8'))

    if hasattr(proc_obj.stderr, 'decode'):
        log_file.write(proc_obj.stderr.decode('utf-8'))

    log_file.close()


def gmx(prog, **kwargs):
    """Uses the subprocess module to run a gmx (GROMACS) program. Returns the
    process object. **kwargs will be passed to subprocess.run
    """
    assert type(prog) == list, "Pass a list of arguments you would use after \
        \"gmx -quiet\"."
    gmx = subprocess.run(['gmx', '-quiet'] + prog, **kwargs)

    return gmx


def makedir(dirname):
    if not os.path.isdir(dirname):
        os.mkdir(dirname)

    else:
        print("Directory \"%s\" already exists. Ignoring this function call!" %
            dirname)
        pass


def unpack(lst=[]):
	"""Unpacks a nested list. Does not care about the deepness of nesting since
		it calls itself recursively.
	"""
	unpacked = []
	for i in lst:
		if type(i) is list:
			unpacked.extend(unpack(i))

		else:
			unpacked.append(i)

	return unpacked


class DataGenerator:
    """Main class containing the methods to generate the data for CC/PBSA.
    Creates a directory with the name of the wildtype (wt) protein and
    subdirectories for each structure ensemble (wt and mutant). If the concoord
    method was called, then each subdirectory will have another layer of
    subdirectories for the individual GROMACS runs.The user will have to pass
    the wt protein .pdb file, a list of mutations, a parameter file containing
    the flags for each programm (CONCOORD/GROMACS) and optionally user specific
    .mdp files and tables for GROMACS.
    """
    def __init__(self,
        wt,
        mutlist,
        flags,
        calculate,
        chains,
        min_mdp,
        energy_mdp,
        mdrun_table,
        pbeparams
    ):
        """Creates and moves to the main folder upon initialization and copies
        the wt .pdb file into a subdirectory of the working directory.
        self.maindir will be the directory to which each function call returns
        to after completing.
        """
#        Attributes on which other classes depend on are marked by empty comment
        self.mode = calculate #
        self.wt = wt #
        self.flags = self.parse_flags(flags)
        self.mut_df = self.parse_mutlist(mutlist) #

        self.e_mdp = energy_mdp
        self.min_mdp = min_mdp
        self.mdrun_table = mdrun_table
        self.pbeparams = pbeparams

#        Parameters to indicate the state of the structures.
        self.minimized = False
        self.chains = chains

#        Initialize working directory.
        makedir(self.__repr__())
        shutil.copy(self.wt, self.__repr__())
        os.chdir(self.__repr__())
        self.maindir = os.getcwd()
        makedir(self.__repr__())
        self.wt = self + ".pdb"
        shutil.move(self.wt, self.__repr__())
        self.wdlist = [self.maindir+'/'+self.__repr__()] #


    def __repr__(self):
        return self.wt.split('/')[-1][:-4]


    def __add__(self, other):
        """Makes it possible to add this object to strings. Not much else makes
        sense at the moment and it makes some of the source syntax more
        readable.
        """
        return self.__repr__() + other


    def __len__(self):
        """Returns the number of structures that would be generated by
        CONCOORD.
        """
        return int(self.flags['CC']['DISCO FLAGS'] \
            [self.flags['CC']['DISCO FLAGS'].index('-n')+1])


    def parse_flags(self, flags_raw):
        """Parse the list of flags in the .txt flag file for the programs.
        Called upon initialization. Returns a
        dictionary in the following format:

        parsed_flags = {
            'CC': {
                'DIST FLAGS': [list of flags],
                'DISCO FLAGS': [list of flags]
            },
            'gmx': {
                'PDB2GMX FLAGS': [list of flags],
                'EDITCONF FLAGS': [list of flags],
                'GROMPP FLAGS': [list of flags],
                'MDRUN FLAGS': [list of flags],
            }
        }
        
        The list can then be extended directly to other input lists for
        subprocess.run().
        """
        parsed_flags = {
            'CC': {
                'DIST FLAGS': [],
                'DISCO FLAGS': []
            },
            'gmx': {
                'PDB2GMX FLAGS': [],
                'EDITCONF FLAGS': [],
                'GROMPP FLAGS': [],
                'MDRUN FLAGS': [],
            }
        }

#        Search for this file just in case it is not in the current directory of
#        the class.
        flag_file = list(open(flags_raw, 'r'))
        content = [line[:line.index('\n')] for line in flag_file]
        uncommented = []

        for line in content:
            if ';' in line:
                line = line[:line.index(';')]

            if len(line) > 0:
                uncommented.append(' '.join(line.split()))

    #    Indexes the file for where the flags are defined
        idx = [uncommented.index(j) for i in parsed_flags.values() for j in i]
        idx.append(len(uncommented))

        i = 0
        for keys, vals in parsed_flags.items():

            for prog in vals:
                parsed_flags[keys][prog] = unpack([i.split('=') for i in \
                    uncommented[idx[i]+1:idx[i+1]]])
                i += 1
    
        return parsed_flags


    def parse_mutlist(self, mutlist_raw):
        """Parse the list of mutations so that .mutate() can perform mutations
        using PyMOL correctly. Called upon initialization.
        The format of the mutation instruction should be (in one letter code
        and without spaces):
            (*Chain_*) *OriginalAA* *ResidueNumber* *NewAA*
        for exapmle:
            - A20G (for a monomer)
            - B_H10I (for a dimer with a chain named \"B\")
        """
#        One letter AA code to three letter code as presented on PyMOL Wiki.
        aa1 = list("ACDEFGHIKLMNPQRSTVWY")
        aa3 = "ALA CYS ASP GLU PHE GLY HIS ILE LYS LEU \
            MET ASN PRO GLN ARG SER THR VAL TRP TYR".split()
        aa123 = dict(zip(aa1,aa3))

        mut_lst = list(open(mutlist_raw, 'r'))
        mut_lst = [mut.split('\n')[0] for mut in mut_lst]

#        Returned object is a pandas dataframe.
        parsed_mut = pd.DataFrame(
            columns=["Chain", "AA", "Residue", "Mutation"], index=mut_lst)

        parsed_mut['Chain'] = [i[0] if '_' in i else '' for i in mut_lst.copy()]
        parsed_mut['AA'] = [i[0] if '_' not in i else i[i.index('_')+1] \
            for i in mut_lst.copy()]
        parsed_mut['Residue'] = [int_in_str(i) for i in mut_lst]
        parsed_mut['Mutation'] = [aa123[i[-1]] for i in mut_lst]

        return parsed_mut


    def mutate(self):
        """Uses PyMOL and the list of mutations to mutate the wildtype protein.
        Does not accuratly mutate if input structure or mutation instructions
        are flawed. WARNING: No specific message is given if that happens. Best
        to check if the residues in the .pdb file are correctly numbered.
        Updates working directories list (wdlist).
        """
        for m in range(len(self.mut_df)):
            cmd.load(self+'/'+self.wt)
            cmd.wizard('mutagenesis')
            mut_key = self.mut_df.axes[0][m]
            cmd.get_wizard().do_select('///%s/%s' %
                (self.mut_df["Chain"][m], str(self.mut_df["Residue"][m])))
            cmd.get_wizard().set_mode(self.mut_df["Mutation"][m])
            cmd.get_wizard().apply()
            cmd.save(mut_key + ".pdb")
            cmd.reinitialize()
            makedir(mut_key)
            shutil.move(mut_key + ".pdb", mut_key)
            self.wdlist.append(self.maindir+'/'+mut_key)


    def gmx2pdb(self, gro='confout.gro'):
        """Use gmx trjconv to turn by default a confout.gro file back into a
        .pdb file. Used as a step before CONCOORD, to avoid bad starting
        structures.
        """
        for d in self.wdlist:
            os.chdir(d)
            fpf = d.split('/')[-1] + '.pdb'
            trjconv = ['trjconv', '-s', gro, '-o', fpf]
            gmx(trjconv, input=b'0')

            os.chdir(self.maindir)


    def concoord(self):
        """Performs the CONCOORD procedure to generate protein structure
        ensembles. Takes additional flags from "flag_parse" as input (pass it
        as "flag_parse_output['CC']"). Make sure that \"CONCOORDRC.bash\" is
        sourced.
        """
        for d in self.wdlist:
            os.chdir(d)
            fpf = d.split('/')[-1] # fpf stands for file prefix
            dist_input = [
                'dist',
                '-p', '%s' % fpf+'.pdb',
                '-op', '%s_dist.pdb' % fpf,
                '-og', '%s_dist.gro' % fpf,
                '-od', '%s_dist.dat' % fpf,
            ]
    
            disco_input = [
                'disco',
                '-d', '%s_dist.dat' % fpf,
                '-p', '%s_dist.pdb' % fpf,
                '-op', '',
                '-or', '%s_disco.rms' % fpf,
                '-of', '%s_disco_Bfac.pdb' % fpf
            ]
            dist_input.extend(self.flags['CC']['DIST FLAGS'])
            disco_input.extend(self.flags['CC']['DISCO FLAGS'])
            subprocess.run(dist_input, input=b'1\n1')
            subprocess.run(disco_input)
            
            for n in range(1, len(self)+1):
                nr = str(n)
                makedir(nr)
#                shutil.copy(fpf+'.pdb', nr+'/'+nr+'.pdb')
                shutil.move(nr+'.pdb', nr)

            os.chdir(self.maindir)

        self.wdlist = [d+'/'+str(n) \
            for d in self.wdlist for n in range(1, len(self)+1)]


    def minimize(self):
        """Minimzes all .pdb files in self.wdlist. Parameters for minimization
        are set at the creation of the object. Default .mdp and .xvg tables are
        given, but can be changed if another path is specified.
        """
        for d in self.wdlist:
            os.chdir(d)
            fpf = d.split('/')[-1]
            gmxprocs = [
                ['pdb2gmx', '-f', fpf+'.pdb'],
                ['editconf'],
                ['grompp', '-f', self.min_mdp],
                ['mdrun', \
                    '-tablep', self.mdrun_table, '-table', self.mdrun_table]
            ]
            gmxprocs = [unpack(list(proc)) \
                for proc in zip(gmxprocs, self.flags['gmx'].values())]

            for proc in gmxprocs:
                gmx(proc)

            if self.mode == 'affinity':
                chain_selection = [b'chain %b\n' % bytes(c, 'utf-8') for c in self.chains]
                chain_selection = b''.join(chain_selection) + b'q\n'
                gmx(['make_ndx', '-f', 'topol.tpr'], input=chain_selection)
#
                for cn in range(len(self.chains)):
                    select = bytes(str(10+cn), 'utf-8')
                    chainbox = "chain_%s.gro" % self.chains[cn]

                    trjconv_select = [
                        'trjconv', '-f', 'out.gro',
                        '-n', 'index.ndx',
                        '-o', chainbox
                    ]
                    gmx(trjconv_select, input=select)

#                    Create the run file for the chain and minimize it.
                    chaintpr = "chain_%s.tpr"  % self.chains[cn]

#                    Change topology file for grompp and mdrun.
                    with open("topol.top", 'r') as topl:
                        l = topl.readlines()

                    topline = cn-len(self.chains)
                    l[-len(self.chains):] = [';' + line if line != l[topline] \
                            else line for line in l[-len(self.chains):]]

                    with open("topol.top", 'w') as topl:
                        topl.writelines(l)

                    gmx(gmxprocs[2] + ['-c', chainbox, '-o', chaintpr])
                    gmx(gmxprocs[3] + ['-s', chaintpr, '-deffnm',
                        "chain_%s_confout" % self.chains[cn]])

#                    Revert the changes in the topol.top file.
                    l[-len(self.chains):] = [line[1:]  if line != l[topline] \
                            else line for line in l[-len(self.chains):]]

                    with open("topol.top", 'w') as topl:
                        topl.writelines(l)

            os.chdir(self.maindir)

        self.minimized = True


    def solvation_energy(self):
        """Uses gropbe for calculating the solvation and Coulomb energy.
        Requires an additional parameters file for epsilon r which .tpr file
        and so on. The run input file line should be left empty since this
        method will specify it for every structure. This method can only be run
        after there is a \"sp.tpr\" (single point) file, i.e. after the lj()
        method, since topol.tpr is still the unminimized structure.
        """
#        assert self.minimized == True, ERRORSTR
        
        for d in self.wdlist:
            os.chdir(d)
            intpr = "in(tpr,%s)" % (d+'/sp.tpr\n')

            with open(self.pbeparams, 'r') as pbeparams:
                lines = pbeparams.readlines()

            with open("gropbe.txt", 'w') as pbeparams:
                pbeparams.writelines([intpr]+lines)

            gropbe = ['gropbe', "gropbe.txt"]
            
            input_ = ",".join([str(i) for i in range(len(self.chains))])
            solv = subprocess.run(gropbe, input=bytes(input_, 'utf-8'),
                stdout=subprocess.PIPE)
            log("solvation.log", solv)

            if self.mode == 'affinity':

                for cn in range(len(self.chains)):
                    chaintpr ="chain_%s.tpr" % self.chains[cn]

                    with open(self.pbeparams, 'r') as pbeparams:
                        lines = pbeparams.readlines()

                    with open("gropbe.txt", 'w') as pbeparams:
                        pbeparams.writelines(["in(tpr,%s)" % chaintpr] + lines)

                    gropbe = ['gropbe', "gropbe.txt"]
                    solv = subprocess.run(gropbe, input=b'0',
                        stdout=subprocess.PIPE)
                    log("solvation_%s.log" % self.chains[cn], solv)

            os.chdir(self.maindir)


    def coulomb_lj(self):
        """calculates the single point Lennard Jones Energy (1-4 and
        shortrange) of all self.structures.
        """
        assert self.minimized == True, ERRORSTR

        for d in self.wdlist:
            os.chdir(d)

            gmxprocs = [
                ['grompp', '-f', self.e_mdp, '-c', "confout.gro", '-o', 'sp.tpr'],
                ['mdrun', '-s', 'sp.tpr',
                    '-rerun', "confout.gro",
                    '-deffnm', 'sp']
            ]

            gmxprocs = [unpack(list(proc)) \
                for proc in zip(gmxprocs, list(self.flags['gmx'].values())[-2:])]

            for proc in gmxprocs:
                gmx(proc)

            coulomb = gmx(['energy', '-f', 'sp.edr', '-sum', 'yes'],
                input=b'6 8', stdout=subprocess.PIPE)
            log("coulomb.log", coulomb)
            lj = gmx(['energy', '-f', 'sp.edr', '-sum', 'yes'],
                input=b'5 7', stdout=subprocess.PIPE)
            log("lj.log", lj)

            if self.mode == 'affinity':

                for cn in range(len(self.chains)):
                    ccout = "chain_%s_confout.gro" % self.chains[cn]
                    chaintpr = "chain_%s_lj.tpr"  % self.chains[cn]

                    with open("topol.top", 'r') as topl:
                        l = topl.readlines()

                    topline = cn-len(self.chains)
                    l[-len(self.chains):] = [';' + line if line != l[topline] \
                            else line for line in l[-len(self.chains):]]

                    with open("topol.top", 'w') as topl:
                        topl.writelines(l)

                    gmxprocs = [
                        ['grompp', '-f', self.e_mdp, '-c', ccout, '-o', chaintpr],
                        ['mdrun', '-s', chaintpr, '-rerun', ccout,
                            '-deffnm', "chain_%s_sp" % self.chains[cn]]
                    ]

                    gmxprocs = [unpack(list(proc)) \
                        for proc in zip(gmxprocs,
                            list(self.flags['gmx'].values())[-2:])]

                    for proc in gmxprocs:
                        gmx(proc)

                    coulomb = gmx(['energy',
                        '-f', 'chain_%s_sp.edr' % self.chains[cn],
                        '-sum', 'yes'], input=b'5 6 7 8', stdout=subprocess.PIPE)
                    log("chain_%s_coulomb.log" % self.chains[cn], coulomb)
                    lj = gmx(['energy',
                        '-f', 'chain_%s_sp.edr' % self.chains[cn],
                        '-sum', 'yes'], input=b'5 6 7 8', stdout=subprocess.PIPE)
                    log("chain_%s_lj.log" % self.chains[cn], lj)

#                    Revert the changes in the topol.top file.
                    l[-len(self.chains):] = [line[1:]  if line != l[topline] \
                            else line for line in l[-len(self.chains):]]

                    with open("topol.top", 'w') as topl:
                        topl.writelines(l)

            os.chdir(self.maindir)


    def area(self):
        """Calculate the solvent accessible surface area and saves it to
        area.xvg. If the mode is set to affinity, only the wt protein structure
        ensemble will be used and the values for the interaction surface will
        be written into the .xvg file
        """
        assert self.minimized == True, ERRORSTR

        sasa = ['sasa', '-s', 'confout.gro']

        for d in self.wdlist:
            os.chdir(d)

            if self.mode == 'affinity':
                gmx(sasa + ['-n', 'index.ndx', '-output', '10', '11'],
                    input=b'0')

            else:
                gmx(sasa, input=b'0')

            os.chdir(self.maindir)


    def schlitter(self):
        """Calculates an upper limit of the entropy according to Schlitter's
        formula. Used in .fullrun() if the mode is stability
        """
        assert self.minimized == True, ERRORSTR
        trjcat = ['trjcat', '-cat', 'yes', '-f']
        covar = ['covar', '-f', 'trajout.xtc', '-nofit', '-nopbc', '-s']
        anaeig = ['anaeig', '-v', 'eigenvec.trr', '-entropy']
        ensembles = unpack([self.__repr__(), list(self.mut_df.index)])
        ensembles = [i for i in ensembles if i != None]

        for en in ensembles:
            os.chdir(en)
            trrs = [self.maindir+'/'+en+'/%d/traj.trr' % (n+1)
                for n in range(len(self))]

            gmx(trjcat+trrs)
            gmx(covar+[self.maindir+'/'+en+"/1/confout.gro"], input=b'0')
            entropy = gmx(anaeig, stdout=subprocess.PIPE)
            log('entropy.log', entropy)

            os.chdir(self.maindir)


    def fullrun(self):
        """Performs a full run based on the list of mutations, number of
        CONCOORD structures and other parameters. Methods used will depend on
        the mode chosen (stability/affinity).
        """
        if self.mode == 'stability':
            self.mutate()
            self.minimize()
            self.gmx2pdb()
            self.concoord()
            self.minimize()
            self.coulomb_lj()
            self.solvation_energy()
            self.area()
            self.schlitter()

        elif self.mode == 'affinity':
            self.mutate()
            self.minimize()
            self.gmx2pdb()
            self.concoord()
            self.minimize()
            self.coulomb_lj()
            self.solvation_energy()
            self.area()


class DataCollector:
    """After a fullrun of DataGenerator, the object can be parsed to this class
    to search for the relevant files in which energy values are supposed to be
    stored. Also contains methods to create .csv tables for calculation of
    folding free energy differences between wildtype and mutant protein.
    """
    aa1 = list("ACDEFGHIKLMNPQRSTVWY")
    aa3 = "ALA CYS ASP GLU PHE GLY HIS ILE LYS LEU MET ASN PRO GLN ARG SER THR VAL TRP TYR".split()
    aa123 = dict(zip(aa1,aa3))
    aa321 = dict(zip(aa3,aa1))

    def __init__(self, data_obj):
        """Pass the DataGenerator object to initialize. This way all the
        directories that contains the data is known without much searching.
        """
        self.maindir = data_obj.maindir
        os.chdir(self.maindir)

        self.n = len(data_obj)
        self.mut_df = data_obj.mut_df
        self.mut_df['Mutation'] = [self.aa321[x] for x in self.mut_df['Mutation']]
        self.mode = data_obj.mode

        if self.mode == 'stability':
            self.G = pd.DataFrame(0.0, 
                columns=['SOLV', 'COUL', 'LJ', 'SAS', '-TS'],
                index=unpack([data_obj.__repr__(), list(data_obj.mut_df.index)])
            )
            self.dG = pd.DataFrame(0.0, 
                columns=['SOLV', 'COUL', 'LJ', 'SAS', '-TS'],
                index=self.G.index[1:]
            )
            self.ddG = pd.DataFrame(0.0,
                columns=['CALC', 'SOLV', 'COUL', 'LJ', 'SAS', '-TS'],
                index=self.G.index[1:]
            )

        else:
            self.G = pd.DataFrame(0.0, 
                columns=['SOLV', 'COUL', 'LJ', 'PPIS', 'PKA'],
                index=unpack([data_obj.__repr__(), list(data_obj.mut_df.index)])
            )


    def __len__(self):
        """Returns the number of structures generated by CONCOORD
        """
        return self.n
        

    def search_lj(self):
        """Find the files in which the Lennard-Jones energies are supposed to
        be written in and save the parsed values in self.G.
        """
        tail = "tail -q -n 1 ".split()
        for d in self.G.index:
            os.chdir(d)
            files = glob.glob("*/lj.log")
            lj = subprocess.run(tail + files, stdout=subprocess.PIPE)
            parsed = [float(i.split()[1]) for i in \
                lj.stdout.decode('utf-8').split('\n') if len(i) > 0]

            if self.mode == 'affinity':
                files = glob.glob("*/chain_*_lj.log")
                solv = subprocess.run(tail + files, stdout=subprocess.PIPE)
                solv = [i for i in solv.stdout.decode('utf-8').split('\n') \
                    if 'kJ/mol' in i]
                parsed = [float(i[:i.index('kJ')].split("y")[1]) for i in solv]
                self.G -= np.array(parsed).sum()

            self.G['LJ'][d] = np.array(parsed).mean()
            os.chdir(self.maindir)


    def search_coulomb(self):
        """Find the file in which the Coulomb energies are supposed to be
        written in and save the parsed values in G.
        """
#        tail = "tail -q -n 1 ".split()
#        for d in self.G.index:
#            os.chdir(d)
#            files = glob.glob("*/coulomb.log")
#            lj = subprocess.run(tail + files, stdout=subprocess.PIPE)
#            parsed = [float(i.split()[1]) for i in \
#                lj.stdout.decode('utf-8').split('\n') if len(i) > 0]
#            self.G['COUL'][d] = np.array(parsed).mean()
#            os.chdir(self.maindir)
#
#            if self.mode == 'affinity':
#                files = glob.glob("*/chain_*_coulomb.log")
#                solv = subprocess.run(tail + files, stdout=subprocess.PIPE)
#                solv = [i for i in solv.stdout.decode('utf-8').split('\n') \
#                    if 'kJ/mol' in i]
#                parsed = [float(i[:i.index('kJ')].split("y")[1]) for i in solv]
#                self.G -= np.array(parsed).sum()
#
#            os.chdir(self.maindir)
        tail = "tail -q -n 5".split()
        for d in self.G.index:
            os.chdir(d)
            files = glob.glob("*/solvation.log")
            coul = subprocess.run(tail + files, stdout=subprocess.PIPE)
            coul = [i for i in coul.stdout.decode('utf-8').split('\n') \
                if 'Coulombic energy' in i]
            parsed = [float(i[:i.index('kJ')].split("=")[1]) for i in coul]
            self.G['COUL'][d] = np.array(parsed).mean()

            if self.mode == 'affinity':
                files = glob.glob("*/solvation_*.log")
                coul = subprocess.run(tail + files, stdout=subprocess.PIPE)
                coul = [i for i in coul.stdout.decode('utf-8').split('\n') \
                    if 'Coulombic energy' in i]
                parsed = [float(i[:i.index('kJ')].split("=")[1]) for i in coul]
                self.G -= np.array(parsed).sum()

            os.chdir(self.maindir)
        


    def search_solvation(self):
        """Find the files in which the solvation energy and the Coulomb
        potential are supposed to be written in and save the parsed values in
        self.G.
        """
        tail = "tail -q -n 3".split()
        for d in self.G.index:
            os.chdir(d)
            files = glob.glob("*/solvation.log")
            solv = subprocess.run(tail + files, stdout=subprocess.PIPE)
            solv = [i for i in solv.stdout.decode('utf-8').split('\n') \
                if 'Solvation Energy' in i]
            parsed = [float(i[:i.index('kJ')].split("y")[1]) for i in solv]
            self.G['SOLV'][d] = np.array(parsed).mean()

            if self.mode == 'affinity':
                files = glob.glob("*/solvation_*.log")
                solv = subprocess.run(tail + files, stdout=subprocess.PIPE)
                solv = [i for i in solv.stdout.decode('utf-8').split('\n') \
                    if 'Solvation Energy' in i]
                parsed = [float(i[:i.index('kJ')].split("y")[1]) for i in solv]
                self.G -= np.array(parsed).sum()

            os.chdir(self.maindir)


    def search_area(self):
        """Find the files in which the (interaction-)area (normally area.xvg)
        potential are supposed to be written in and save the parsed values in
        G.
        """
        tail = "tail -q -n 1 ".split()

        if self.mode == 'stability':

            for d in self.G.index:
                os.chdir(d)
                files = glob.glob("*/area.xvg")
                areas = subprocess.run(tail + files, stdout=subprocess.PIPE)
                parsed = [float(i.split()[1]) for i in \
                    areas.stdout.decode('utf-8').split('\n') if len(i) > 0]
                self.G['SAS'][d] = np.array(parsed).mean()
                os.chdir(self.maindir)

        if self.mode == 'affinity':
            os.chdir(self.G.index[0])
            files = glob.glob("*/area.xvg")
            areas = subprocess.run(tail + files, stdout=subprocess.PIPE)
            parsed = [i.split()[1:] for i in \
                areas.stdout.decode('utf-8').split('\n') if len(i) > 0]
            parsed = [(float(i[1])+float(i[2])-float(i[0])) for i in parsed]
            self.G['PPIS'] = np.array(parsed).mean()
            os.chdir(self.maindir)


    def search_entropy(self):
        """Find the files in which the entropy according the Schlitter's
        formula are supposed to be written in and save the parsed values in
        self.G.
        """
        head = "head -n 1 entropy.log".split()
        for d in self.G.index:
            os.chdir(d)
            entropy = subprocess.run(head, stdout=subprocess.PIPE)
            entropy = entropy.stdout.decode('utf-8')
            valstart = entropy.index('is ')+3
            valend = entropy.index(' J/mol K')
            entropy = float(entropy[valstart:valend])/1000 # J/mol K-->kJ/mol K
            self.G['-TS'][d] = np.array(-298.15 * entropy)
            os.chdir(self.maindir)


    def search_data(self):
        """Uses all the previous searching methods to create a .csv file of the
        DataFrame object.
        """
        if self.mode == 'stability':
            self.search_lj()
            self.search_coulomb()
            self.search_solvation()
            self.search_area()
            self.search_entropy()
            self.G.to_csv("G.csv")

        if self.mode == 'affinity':
            self.search_lj()
            self.search_coulomb()
            self.search_solvation()
            self.search_area()
            self.G.to_csv("G.csv")


    def dstability(self, gxg_table):
        """Calculate the free energy difference between folded and unfolded
        state based on the energy table passed. ddstability operates
        independent of this function. This is just used for additional info.
        """
        gxgtable = pd.read_csv(gxg_table, index_col=0)
        
        for i in self.ddG.index:
            aa_wt = "G%sG" % i.split('_')[-1][-1]
            aa_mut = "G%sG" % i.split('_')[-1][0]
            self.dG['SOLV'][i] = self.G['SOLV'][i] - self.G['SOLV'][0]

            self.dG['COUL'][i] = self.G['COUL'][i] - self.G['COUL'][0]

            self.dG['LJ'][i] = self.G['LJ'][i] - self.G['LJ'][0]

            self.dG['SAS'][i] = self.G['SAS'][i] - self.G['SAS'][0]

            self.dG['-TS'][i] = self.G['-TS'][i] - self.G['-TS'][0]

        self.dG.to_csv("dG.csv")


    def daffinity(self, gxg_table):
        """Calculate the free energy difference between folded and unfolded
        state based on the energy table passed. ddstability operates
        independent of this function. This is just used for additional info.
        """
        gxgtable = pd.read_csv(gxg_table, index_col=0)
        
        for i in self.ddG.index:
            aa_wt = "G%sG" % i.split('_')[-1][-1]
            aa_mut = "G%sG" % i.split('_')[-1][0]
            self.dG['SOLV'][i] = self.G['SOLV'][i] - self.G['SOLV'][0]

            self.dG['COUL'][i] = self.G['COUL'][i] - self.G['COUL'][0]

            self.dG['LJ'][i] = self.G['LJ'][i] - self.G['LJ'][0]

        self.dG.to_csv("dG.csv")


    def ddstability(self, gxg_table, alpha, beta, gamma, tau):
        """Calculate the folding free energy difference. For the stability
        calculation, a table with values of GXG tripeptides needs to be
        supplied.
        """
        gxgtable = pd.read_csv(gxg_table, index_col=0)
        
        for i in self.ddG.index:
            aa_wt = "G%sG" % i.split('_')[-1][-1]
            aa_mut = "G%sG" % i.split('_')[-1][0]
            self.ddG['SOLV'][i] = alpha * \
                 (self.G['SOLV'][i] - self.G['SOLV'][0] - \
                 gxgtable['SOLV'][aa_mut] + gxgtable['SOLV'][aa_wt])

            self.ddG['COUL'][i] = alpha * \
                 (self.G['COUL'][i] - self.G['COUL'][0] - \
                 gxgtable['COUL'][aa_mut] + gxgtable['COUL'][aa_wt])

            self.ddG['LJ'][i] = beta * \
                 (self.G['LJ'][i] - self.G['LJ'][0] - \
                 gxgtable['LJ'][aa_mut] + gxgtable['LJ'][aa_wt])

            self.ddG['SAS'][i] = gamma * \
                 (self.G['SAS'][i] - self.G['SAS'][0] - \
                 gxgtable['SAS'][aa_mut] + gxgtable['SAS'][aa_wt])

            self.ddG['-TS'][i] = tau * \
                 (self.G['-TS'][i] - self.G['-TS'][0] - \
                 gxgtable['-TS'][aa_mut] + gxgtable['-TS'][aa_wt])

            self.ddG['CALC'][i] =self.ddG['SOLV'][i] +self.ddG['COUL'][i] + \
                self.ddG['LJ'][i] +self.ddG['SAS'][i] +self.ddG['-TS'][i]

        self.ddG.to_csv("ddG.csv")


    def ddaffinity(self, alpha, beta, gamma, c, pka):
        """Calculate the change in affinity
        """
        self.ddG = pd.DataFrame(0.0,
            columns=['CALC', 'SOLV', 'COUL', 'LJ', 'SAS', '-TS'],
            index=self.G.index[1:]
        )
        
        for i in self.ddG.index:
            self.ddG['SOLV'][i] = alpha * \
                 (self.G['SOLV'][i] - self.G['SOLV'][0])

            self.ddG['COUL'][i] = alpha * \
                 (self.G['COUL'][i] - self.G['COUL'][0])

            self.ddG['LJ'][i] = beta * \
                 (self.G['LJ'][i] - self.G['LJ'][0])

            self.ddG['CALC'][i] =self.ddG['SOLV'][i] +self.ddG['COUL'][i] + \
                self.ddG['LJ'][i] + gamma*ddG['PPIS'][i]+ c +self.ddG['PKA'][i]

        self.ddG.to_csv("ddG.csv")


class GXG(DataGenerator, DataCollector):
    """Subclassed from DataGenerator. Used to create the GXG look-up tables for
    stability calculations.
    """
    aa1 = list("ACDEFGHIKLMNPQRSTVWY")

    def __init__(
        self,
        gxg_flags,
        min_mdp,
        energy_mdp,
        mdrun_table,
        pbeparams
    ):
        """Only takes the flags and .mdp arguments, since the others should not
        affect it anyway.
        """
        self.mode = 'stability'
        self.flags = self.parse_flags(gxg_flags)
        self.e_mdp = energy_mdp
        self.min_mdp = min_mdp
        self.mdrun_table = mdrun_table
        self.pbeparams = pbeparams
        self.minimized = False
        self.mut_df = pd.DataFrame(0, columns=['X'],
            index=["G%sG" % x for x in self.aa1])
        self.chains = ['A']

        makedir('GXG')
        os.chdir('GXG')
        self.maindir = os.getcwd()
        self.make_gxg()
        self.wdlist = [self.maindir+'/G%sG' % x for x in self.aa1]

        self.G = pd.DataFrame(0.0,
            columns=['SOLV', 'COUL', 'LJ', 'SAS', '-TS'],
            index=["G%sG" % x for x in self.aa1])

    
    def __repr__(self):
        return None


    def make_gxg(self):
        """Create the tripeptides using PyMOL. Each peptide is saved in a
        separate directory similar to mutations in Protein.
        """
        for x in self.aa1:
            gxg = "G%sG" % x
            makedir(gxg)
            cmd.fab(gxg, gxg)
            cmd.save(gxg+'/'+gxg+'.pdb')
            cmd.reinitialize()


    def __call__(self):
        self.concoord()
        self.minimize()
        self.coulomb_lj()
        self.solvation_energy()
        self.area()
        self.schlitter()
        self.search_lj()
        self.search_coulomb()
        self.search_solvation()
        self.search_area()
        self.search_entropy()
