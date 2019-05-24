"""Code for CC/PBSA, a fast tool for estimating folding free energy
differences.
"""
import glob
import os
import re
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
    log_file.write(proc_obj.stdout.decode('utf-8'))
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
    def __init__(self, wt, mutlist, flags, calculate='stability', chains=['A'],
        min_mdp="/Users/linkai/CC_PBSA/min.mdp",
        energy_mdp="/Users/linkai/CC_PBSA/energy.mdp",
        mdrun_table="/Users/linkai/CC_PBSA/table4r-6-12.xvg"
        pbsparams="/Users/linkai/CC_PBSA/parameters.txt"
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
        self.pbsparams = pbsparams

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
#            name = "%s_%s" % (self, mut_key)
            cmd.get_wizard().do_select('///%s/%s' %
                (self.mut_df["Chain"][m], str(self.mut_df["Residue"][m])))
            cmd.get_wizard().set_mode(self.mut_df["Mutation"][m])
            cmd.get_wizard().apply()
            cmd.save(mut_key + ".pdb")
            cmd.reinitialize()
            makedir(mut_key)
            shutil.move(mut_key + ".pdb", mut_key)
            self.wdlist.append(self.maindir+'/'+mut_key)

    
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


    def electrostatics(self):
        """Uses gropbs for calculating the solvation and Coulomb energy.
        Requires an additional parameters file for epsilon r which .tpr file
        and so on. The run input file line should be left empty since this
        method will specify it for every structure.
        """
#        assert self.minimized == True, ERRORSTR
        
        for d in self.wdlist:
            os.chdir(d)

            intpr = "in(tpr,%s)" % d+'/sp.tpr'
            with open(self.pbsparams, 'a') as pbsparams:
                lines = pbsparams.readlines()
                pbsparams.write(intpr)

            gropbs = ['gropbs', self.pbsparams]
            elecstat = subprocess.run(gropbs, stdout=subprocess.PIPE)
            log("elecstat.log", elecstat)

            with open(self.pbsparams, 'w') as pbsparams:
                pbsparams.write(lines)

            os.chdir(self.maindir)

    def lj(self):
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

            energy = gmx(['energy', '-f', 'sp.edr', '-sum', 'yes'],
                input=b'5\n7', stdout=subprocess.PIPE)
            log("lj.log", energy)

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

                    energy = gmx(['energy',
                        '-f', 'chain_%s_sp.edr' % self.chains[cn],
                        '-sum', 'yes'], input=b'5\n7', stdout=subprocess.PIPE)
                    log("chain_%s_lj.log" % self.chains[cn], energy)

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
            self.concoord()
            self.minimize()
            self.electrostatics()
            self.lj()
            self.area()
            self.schlitter()

        elif self.mode == 'affinity':
            self.mutate()
            self.concoord()
            self.minimize()
            self.electrostatics()
            self.lj()
            self.area()


class DataCollector:
    """After a fullrun of DataGenerator, the object can be parsed to this class
    to search for the relevant files in which energy values are supposed to be
    stored. Also contains methods to create .csv tables for calculation of
    folding free energy differences between wildtype and mutant protein.
    """
    def __init__(self, data_obj):
        """Pass the DataGenerator object to initialize. This way all the
        directories that contains the data is known without much searching.
        """
        pattern = re.compile("<class '__.+__.DataGenerator'>")
        assert pattern.match(str(type(data_obj))), "Not a DataGenerator."

        self.maindir = data_obj.maindir
        os.chdir(self.maindir)

        self.n = len(data_obj)

        if data_obj.mode == 'stability':
            self.ener_df = pd.DataFrame(0.0, 
                columns=['SOLV', 'COUL', 'LJ', 'SAS', '-TS'],
                index=unpack([data_obj.__repr__(), list(data_obj.mut_df.index)])
            )

        else:
            self.ener_df = pd.DataFrame(0.0, 
                columns=['SOLV', 'COUL', 'LJ', 'PPIS', 'PKA'],
                index=unpack([data_obj.__repr__(), list(data_obj.mut_df.index)])
            )


    def __len__(self):
        """Returns the number of structures generated by CONCOORD
        """
        return self.n
        

    def search_lj(self):
        """Find the files in which the Lennard-Jones energies are supposed to
        be written in and save the parsed values in ener_df.
        """
        tail = "tail -q -n 1 ".split()
        for d in self.ener_df.index:
            os.chdir(d)
            files = glob.glob("*/lj.log")
            lj = subprocess.run(tail + files, stdout=subprocess.PIPE)
            parsed = [float(i.split()[1]) for i in \
                lj.stdout.decode('utf-8').split('\n') if len(i) > 0]
            self.ener_df['LJ'][d] = np.array(parsed).mean()
            os.chdir(self.maindir)


    def search_elecstat(self):
        """Find the files in which the solvation energy and the Coulomb
        potential are supposed to be written in and save the parsed values in
        ener_df.
        """
        for d in self.ener_df.index:
            pass


    def search_area(self):
        """Find the files in which the (interaction-)area (normally area.xvg)
        potential are supposed to be written in and save the parsed values in
        ener_df.
        """
        tail = "tail -q -n 1 ".split()
        for d in self.ener_df.index:
            os.chdir(d)
            files = glob.glob("*/area.xvg")
            areas = subprocess.run(tail + files, stdout=subprocess.PIPE)
            parsed = [float(i.split()[1]) for i in \
                areas.stdout.decode('utf-8').split('\n') if len(i) > 0]
            self.ener_df['SAS'][d] = np.array(parsed).mean()
            os.chdir(self.maindir)


    def search_entropy(self):
        """Find the files in which the entropy according the Schlitter's
        formula are supposed to be written in and save the parsed values in
        ener_df.
        """
        head = "head -n 1 entropy.log".split()
        for d in self.ener_df.index:
            os.chdir(d)
            entropy = subprocess.run(head, stdout=subprocess.PIPE)
            entropy = entropy.stdout.decode('utf-8')
            valstart = entropy.index('is ')+3
            valend = entropy.index(' J/mol')
            entropy = float(entropy[valstart:valend])
            self.ener_df['-TS'][d] = np.array(-300 * entropy)
            os.chdir(self.maindir)


    def ffed(self, alpha=0, beta=0, gamma=0, tau=0, c=0):
        """Calculate the folding free energy difference. For the stability
        calculation, a table with values of GXG tripeptides needs to be
        supplied.
        """
        ddG = pd.DataFrame(0.0,
            columns=['CALC', 'SOLV', 'COUL', 'LJ', 'SAS', '-TS'],
            index=self.ener_df.index[1:]
        )
        
        for i in ddG.index:
            ddG['SOLV'][i] = alpha * (self.ener_df['SOLV'][i] - \
                self.ener_df['SOLV'][0])
            ddG['COUL'][i] = alpha * (self.ener_df['COUL'][i] - \
                self.ener_df['COUL'][0])
            ddG['LJ'][i] = beta * (self.ener_df['LJ'][i] - \
                self.ener_df['LJ'][0])
            ddG['SAS'][i] = gamma * (self.ener_df['SAS'][i] - \
                self.ener_df['SAS'][0])
            ddG['-TS'][i] = tau * (self.ener_df['-TS'][i] - \
                self.ener_df['-TS'][0])
            ddG['CALC'][i] = ddG['SOLV'][i] + ddG['COUL'][i] + \
                ddG['LJ'][i] + ddG['SAS'][i] + ddG['-TS'][i]


if __name__ == '__main__':
#    x = DataGenerator("1bxi.pdb", "mut.txt", "param.txt", calculate='affinity',
#        chains=['A', 'B'])
#    x.fullrun()
#
#    os.chdir('..')

    y = DataGenerator("1stn.pdb", "mut3.txt", "param.txt")
    y.fullrun()
#    y.mutate()
#    y.concoord()
#    y.minimize()
#    y.lj()
#    y.area()
#    y.schlitter()
    x = DataCollector(y)
    print("values")
    x.search_lj()
    x.search_area()
    x.search_entropy()
    print(x.ener_df)
    x.ffed(1, 1, 1, 1, 1)
    
#    for i in x.wdlist:
#        print(i[len(x.maindir):])
