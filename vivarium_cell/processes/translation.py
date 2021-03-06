'''
======================
Stochastic Translation
======================
'''

import copy
import numpy as np
import random
import logging as log
from arrow import StochasticSystem

from vivarium.core.process import Process
from vivarium.core.experiment import pp
from vivarium_cell.data.amino_acids import amino_acids
from vivarium_cell.data.molecular_weight import molecular_weight
from vivarium_cell.library.polymerize import Elongation, Polymerase, Template, build_stoichiometry, all_products, generate_template

class Ribosome(Polymerase):
    pass

class Transcript(Template):
    pass

def shuffle(l):
    l = [item for item in l]
    np.random.shuffle(l)
    return l

def random_string(alphabet, length):
    string = ''
    for step in range(length):
        string += random.choice(alphabet)
    return string

#: Variable name for unbound ribosomes
UNBOUND_RIBOSOME_KEY = 'Ribosome'

monomer_symbols = []
monomer_ids = []

for symbol, id in amino_acids.items():
    monomer_symbols.append(symbol)
    monomer_ids.append(id)

A = random_string(monomer_symbols, 20)
Z = random_string(monomer_symbols, 60)
B = random_string(monomer_symbols, 30)
Y = random_string(monomer_symbols, 40)

def gather_genes(affinities):
    genes = {}
    for operon, product in affinities.keys():
        if not operon in genes:
            genes[operon] = []
        genes[operon].append(product)
    return genes

def transcripts_to_gene_counts(transcripts, operons):
    counts = {}
    for transcript, genes in operons.items():
        for gene in genes:
            counts[(transcript, gene)] = transcripts.get(transcript, 0)
    return counts

class Translation(Process):

    name = 'translation'
    defaults = {

        'sequences': {
            ('oA', 'eA'): A,
            ('oAZ', 'eA'): A,
            ('oAZ', 'eZ'): Z,
            ('oB', 'eB'): B,
            ('oBY', 'eB'): B,
            ('oBY', 'eY'): Y},

        'templates': {
            ('oA', 'eA'): generate_template(('oA', 'eA'), 20, ['eA']),
            ('oAZ', 'eA'): generate_template(('oAZ', 'eA'), 20, ['eA']),
            ('oAZ', 'eZ'): generate_template(('oAZ', 'eZ'), 60, ['eZ']),
            ('oB', 'eB'): generate_template(('oB', 'eB'), 30, ['eB']),
            ('oBY', 'eB'): generate_template(('oBY', 'eB'), 30, ['eB']),
            ('oBY', 'eY'): generate_template(('oBY', 'eY'), 40, ['eY'])},

        'transcript_affinities': {
            ('oA', 'eA'): 1.0,
            ('oAZ', 'eA'): 2.0,
            ('oAZ', 'eZ'): 5.0,
            ('oB', 'eB'): 1.0,
            ('oBY', 'eB'): 2.0,
            ('oBY', 'eY'): 5.0},

        'elongation_rate': 5.0,
        'polymerase_occlusion': 10,
        'symbol_to_monomer': amino_acids,
        'monomer_ids': monomer_ids,
        'concentration_keys': [],

        'mass_deriver_key': 'mass_deriver',
        'concentrations_deriver_key': 'translation_concentrations',
        'time_step': 1.0,
    }

    def __init__(self, initial_parameters=None):
        '''A stochastic translation model

        .. WARNING::
            Vivarium's knowledge base uses the gene name to name the
            protein. This means that for a gene acrA that codes for
            protein ArcA, you must refer to the gene, transcript, and
            protein each as acrA.

        .. DANGER::
            This documentation will need to be updated to reflect the
            changes in `#185
            <https://github.com/CovertLab/vivarium/pull/185>`_

        :term:`Ports`:

        * **ribosomes**: Expects the ``ribosomes`` variable, whose
          value is a list of the configurations of the ribosomes
          currently active.
        * **molecules**: Expects variables for each of the RNA
          nucleotides.
        * **transcripts**: Expects variables for each transcript to
          translate. Translation will read transcripts from this port.
        * **proteins**: Expects variables for each protein product. The
          produced proteins will be added to this port as counts.
        * **concentrations**: Expects variables for each key in
          ``concentration_keys``. This will be used by a :term:`deriver`
          to convert counts to concentrations.

        Arguments:
            initial_parameters: A dictionary of configuration options.
                Accepts the following keys:

                * **sequences** (:py:class:`dict`): Maps from operon
                  name to the RNA sequence of the operon, as a
                  :py:class:`str`.
                * **templates** (:py:class:`dict`): Maps from the name
                  of an transcript to a :term:`template specification`.
                  The template specification may be generated by
                  :py:func:`cell.library.polymerize.generate_template`
                  like so:

                  >>> from vivarium_cell.library.polymerize import (
                  ...     generate_template)
                  >>> from vivarium.library.pretty import format_dict
                  >>> terminator_index = 5
                  >>> template = generate_template(
                  ...     'oA', terminator_index, ['product1'])
                  >>> print(format_dict(template))
                  {
                      "direction": 1,
                      "id": "oA",
                      "position": 0,
                      "sites": [],
                      "terminators": [
                          {
                              "position": 5,
                              "products": [
                                  "product1"
                              ],
                              "strength": 1.0
                          }
                      ]
                  }


                * **transcript_affinities** (:py:class:`dict`): A map
                  from the name of a transcript to the binding affinity
                  (a :py:class:`float`) of the ribosome for the
                  transcript.
                * **elongation_rate** (:py:class:`float`): The
                  elongation rate of the ribosome.

                  .. todo:: Units of elongation rate

                * **polymerase_occlusion** (:py:class:`int`): The number
                  of base pairs behind the polymerase where another
                  polymerase is occluded and so cannot bind.
                * **symbol_to_monomer** (:py:class:`dict`): Maps from
                  the symbols used to represent monomers in the RNA
                  sequence to the name of the free monomer. This should
                  generally be
                  :py:data:`cell.data.amino_acids.amino_acids`.
                * **monomer_ids** (:py:class:`list`): A list of the
                  names of the free monomers consumed by translation.
                  This can generally be computed as:

                  >>> import pprint
                  >>>
                  >>> from vivarium_cell.data.amino_acids import amino_acids
                  >>> monomer_ids = amino_acids.values()
                  >>> pp = pprint.PrettyPrinter()
                  >>> pp.pprint(list(monomer_ids))
                  ['Alanine',
                   'Arginine',
                   'Asparagine',
                   'Aspartate',
                   'Cysteine',
                   'Glutamate',
                   'Glutamine',
                   'Glycine',
                   'Histidine',
                   'Isoleucine',
                   'Leucine',
                   'Lysine',
                   'Methionine',
                   'Phenylalanine',
                   'Proline',
                   'Serine',
                   'Threonine',
                   'Tryptophan',
                   'Tyrosine',
                   'Valine']

                  Note that we only included the `list()` transformation
                  to make the output prettier. The `dict_values` object
                  returned by `.values()` is sufficiently list-like for
                  use here. Also note that :py:mod:`pprint` just makes
                  the output prettier.
                * **concentration_keys** (:py:class:`list`): A list of
                  variables you want to be able to access as
                  concentrations from the *concentrations* port. The
                  actual conversion is handled by a deriver.

        Example configuring the process (uses
        :py:func:vivarium.library.pretty.format_dict):

        >>> from vivarium.library.pretty import format_dict
        >>> from vivarium_cell.data.amino_acids import amino_acids
        >>> from vivarium_cell.library.polymerize import generate_template
        >>> random.seed(0)  # Needed because process is stochastic
        >>> np.random.seed(0)
        >>> configurations = {
        ...     'sequences': {
        ...         ('oA', 'eA'): 'AWDPT',
        ...         ('oAZ', 'eZ'): 'YVEGELENGGMFISC',
        ...     },
        ...     'templates': {
        ...         ('oA', 'eA'): generate_template(('oA', 'eA'), 5, ['eA']),
        ...         ('oAZ', 'eZ'): generate_template(('oAZ', 'eZ'), 15, ['eA', 'eZ']),
        ...     },
        ...     'transcript_affinities': {
        ...         ('oA', 'eA'): 1.0,
        ...         ('oAZ', 'eZ'): 1.0,
        ...     },
        ...     'elongation_rate': 10.0,
        ...     'polymerase_occlusion': 10,
        ...     'symbol_to_monomer': amino_acids,
        ...     'monomer_ids': amino_acids.values(),
        ...     'concentration_keys': []
        ... }
        >>> # make the translation process, and initialize the states
        >>> translation = Translation(configurations)  # doctest:+ELLIPSIS
        >>> states = {
        ...     'ribosomes': {},
        ...     'molecules': {},
        ...     'proteins': {UNBOUND_RIBOSOME_KEY: 2},
        ...     'transcripts': {
        ...         'oA': 10,
        ...         'oAZ': 10,
        ...     }
        ... }
        >>> states['molecules'].update(
        ...     {
        ...         molecule_id: 100
        ...         for molecule_id in translation.monomer_ids
        ...     }
        ... )
        >>> update = translation.next_update(1, states)
        >>> print(update['ribosomes'])
        {'_add': [{'path': (1,), 'state': <class 'vivarium_cell.processes.translation.Ribosome'>: {'id': 1, 'state': 'occluding', 'position': 9, 'template': ('oAZ', 'eZ'), 'template_index': 0, 'terminator': 0}}, {'path': (2,), 'state': <class 'vivarium_cell.processes.translation.Ribosome'>: {'id': 2, 'state': 'occluding', 'position': 9, 'template': ('oAZ', 'eZ'), 'template_index': 0, 'terminator': 0}}], '_delete': []}
        '''

        if not initial_parameters:
            initial_parameters = {}

        self.monomer_symbols = list(amino_acids.keys())
        self.monomer_ids = list(amino_acids.values())

        self.default_parameters = copy.deepcopy(self.defaults)

        templates = self.or_default(initial_parameters, 'templates')

        self.default_parameters['protein_ids'] = all_products({
            key: Template(config)
            for key, config in templates.items()})

        self.default_parameters['transcript_order'] = list(
            initial_parameters.get(
                'transcript_affinities',
                self.default_parameters['transcript_affinities']).keys())
        self.default_parameters['molecule_ids'] = self.monomer_ids

        self.parameters = copy.deepcopy(self.default_parameters)
        self.parameters.update(initial_parameters)

        self.sequences = self.parameters['sequences']
        self.templates = self.parameters['templates']

        self.transcript_affinities = self.parameters['transcript_affinities']
        self.operons = gather_genes(self.transcript_affinities)
        self.operon_order = list(self.operons.keys())
        self.transcript_order = self.parameters['transcript_order']
        self.transcript_count = len(self.transcript_order)

        self.monomer_ids = self.parameters['monomer_ids']
        self.molecule_ids = self.parameters['molecule_ids']
        self.molecule_ids.extend(['ATP', 'ADP'])

        self.protein_ids = self.parameters['protein_ids']
        self.symbol_to_monomer = self.parameters['symbol_to_monomer']
        self.elongation = 0
        self.elongation_rate = self.parameters['elongation_rate']
        self.polymerase_occlusion = self.parameters['polymerase_occlusion']
        self.concentration_keys = self.parameters['concentration_keys']

        self.affinity_vector = np.array([
            self.transcript_affinities[transcript_key]
            for transcript_key in self.transcript_order], dtype=np.float64)

        self.stoichiometry = build_stoichiometry(self.transcript_count)

        self.initiation = StochasticSystem(self.stoichiometry)

        self.ribosome_id = 0

        self.protein_keys = self.concentration_keys + self.protein_ids
        self.all_protein_keys = self.protein_keys + [UNBOUND_RIBOSOME_KEY]

        self.mass_deriver_key = self.or_default(initial_parameters, 'mass_deriver_key')
        self.concentrations_deriver_key = self.or_default(
            initial_parameters, 'concentrations_deriver_key')

        log.info('translation parameters: {}'.format(self.parameters))

        super(Translation, self).__init__(self.parameters)

    def ports_schema(self):

        def add_mass(schema, masses, key):
            if '_properties' not in schema:
                schema['_properties'] = {}
            if key in masses:
                schema['_properties']['mw'] = masses[key]
            return schema

        return {
            'ribosomes': {
                '*': {
                    'id': {
                        '_default': -1,
                        '_updater': 'set'},
                    'domain': {
                        '_default': 0,
                        '_updater': 'set'},
                    'state': {
                        '_default': None,
                        '_updater': 'set',
                        '_emit': True},
                    'position': {
                        '_default': 0,
                        '_updater': 'set',
                        '_emit': True},
                    'template': {
                        '_default': None,
                        '_updater': 'set',
                        '_emit': True},
                    'template_index': {
                        '_default': 0,
                        '_updater': 'set',
                        '_emit': True}}},

            'global': {},

            'molecules': {
                molecule: add_mass({
                    '_emit': True,
                    '_default': 0,
                    '_divider': 'split'}, molecular_weight, molecule)
                for molecule in self.molecule_ids},

            'transcripts': {
                transcript: add_mass({
                    '_default': 0,
                    '_divider': 'split'}, molecular_weight, transcript)
                for transcript in list(self.operons.keys())},

            'proteins': {
                protein: add_mass({
                    '_default': 0,
                    '_divider': 'split',
                    '_emit': True}, molecular_weight, protein)
                for protein in self.all_protein_keys},

            'concentrations': {
                molecule: {
                    '_default': 0.0,
                    '_updater': 'set'}
                for molecule in self.protein_keys}}

    def derivers(self):
        return {
            self.mass_deriver_key: {
                'deriver': 'mass_deriver',
                'port_mapping': {
                    'global': 'global'}},
            self.concentrations_deriver_key: {
                'deriver': 'concentrations_deriver',
                'port_mapping': {
                    'global': 'global',
                    'counts': 'proteins',
                    'concentrations': 'concentrations'},
                'config': {
                    'concentration_keys': self.protein_keys}}}

    def next_update(self, timestep, states):
        molecules = states['molecules']
        transcripts = states['transcripts']
        proteins = states['proteins']

        ribosomes = {
            id: Ribosome(ribosome)
            for id, ribosome in states['ribosomes'].items()}

        original_ribosome_keys = ribosomes.keys()

        gene_counts = np.array(
            list(transcripts_to_gene_counts(transcripts, self.operons).values()),
            dtype=np.int64)

        # Find out how many transcripts are currently blocked by a
        # newly initiated ribosome
        bound_transcripts = np.zeros(self.transcript_count, dtype=np.int64)
        ribosomes_by_transcript = {
            transcript_key: []
            for transcript_key in self.transcript_order}
        for ribosome in ribosomes.values():
            ribosomes_by_transcript[ribosome.template].append(ribosome)
        for index, transcript in enumerate(self.transcript_order):
            bound_transcripts[index] = len([
                ribosome
                for ribosome in ribosomes_by_transcript[transcript]
                if ribosome.is_bound()])

        # Make the state for a gillespie simulation out of total number of each
        # transcript not blocked by a bound ribosome, concatenated with the number
        # of each transcript that is bound by a ribosome.
        # These are the two states for each transcript the simulation
        # will operate on, essentially going back and forth between
        # bound and unbound states.

        original_unbound_ribosomes = proteins[UNBOUND_RIBOSOME_KEY]
        monomer_limits = {
            monomer: molecules[monomer]
            for monomer in self.monomer_ids}
        unbound_ribosomes = original_unbound_ribosomes

        templates = {
            key: Template(template)
            for key, template in self.templates.items()}

        time = 0
        now = 0
        elongation = Elongation(
            self.sequences,
            templates,
            monomer_limits,
            self.symbol_to_monomer,
            self.elongation)

        while time < timestep:
            # build the state vector for the gillespie simulation
            substrate = np.concatenate([
                gene_counts - bound_transcripts,
                bound_transcripts,
                [unbound_ribosomes]])

            # find number of monomers until next terminator
            distance = 1 / self.elongation_rate

            # find interval of time that elongates to the point of the next terminator
            interval = min(distance, timestep - time)

            if interval == distance:
                # perform the elongation until the next event
                terminations, monomer_limits, ribosomes = elongation.step(
                    interval,
                    monomer_limits,
                    ribosomes)
                unbound_ribosomes += terminations
            else:
                elongation.store_partial(interval)
                terminations = 0

            # run simulation for interval of time to next terminator
            result = self.initiation.evolve(
                interval,
                substrate,
                self.affinity_vector)

            # go through each event in the simulation and update the state
            ribosome_bindings = 0
            for now, event in zip(result['time'], result['events']):
                # ribosome has bound the transcript
                transcript_key = self.transcript_order[event]
                bound_transcripts[event] += 1

                self.ribosome_id += 1
                new_ribosome = Ribosome({
                    'id': self.ribosome_id,
                    'template': transcript_key,
                    'position': 0})
                new_ribosome.bind()
                new_ribosome.start_polymerizing()
                ribosomes[new_ribosome.id] = new_ribosome

                ribosome_bindings += 1
                unbound_ribosomes -= 1

            # deal with occluding rnap
            for ribosome in ribosomes.values():
                if ribosome.is_unoccluding(self.polymerase_occlusion):
                    bound_transcripts[ribosome.template_index] -= 1
                    ribosome.unocclude()

            time += interval

        # track how far elongation proceeded to start from next iteration
        self.elongation = elongation.elongation - int(elongation.elongation)

        proteins = {
            UNBOUND_RIBOSOME_KEY: unbound_ribosomes - original_unbound_ribosomes}
        proteins.update(elongation.complete_polymers)

        molecules = {
            key: count * -1
            for key, count in elongation.monomers.items()}

        original = set(original_ribosome_keys)
        current = set(ribosomes.keys())
        bound_ribosomes = current - original
        completed_ribosomes = original - current
        continuing_ribosomes = original - completed_ribosomes

        # ATP hydrolysis cost is 2 per amino acid elongation
        molecules['ATP'] = 0
        molecules['ADP'] = 0
        for count in elongation.monomers.values():
            molecules['ATP'] -= 2 * count
            molecules['ADP'] += 2 * count

        ribosome_updates = {
            id: ribosomes[id]
            for id in continuing_ribosomes}

        add_ribosomes = [
            {'path': (bound,), 'state': ribosomes[bound]}
            for bound in bound_ribosomes]

        delete_ribosomes = [
            (completed,)
            for completed in completed_ribosomes]

        ribosome_updates['_add'] = add_ribosomes
        ribosome_updates['_delete'] = delete_ribosomes

        update = {
            'ribosomes': ribosome_updates,
            'molecules': molecules,
            'proteins': proteins}

        return update


def test_translation():
    parameters = {}
    translation = Translation(parameters)

    states = {
        'ribosomes': {},
        'molecules': {'ATP': 100000},
        'proteins': {UNBOUND_RIBOSOME_KEY: 10},
        'transcripts': {
            'oA': 10,
            'oAZ': 10,
            'oB': 10,
            'oBY': 10}}
    states['molecules'].update({
        molecule_id: 100
        for molecule_id in translation.monomer_ids})

    update = translation.next_update(10.0, states)

    pp(update)
    print('complete!')



if __name__ == '__main__':
    test_translation()

