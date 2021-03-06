import spacy
from .errors import WrongModelDeserializationError, DocumentTooBigError
from spacy.tokens import Token, Doc
from abc import ABC, abstractmethod
import jsonpickle


class SemanticDependency:
    """A labelled semantic dependency between two tokens."""

    def __init__(self, parent_index, child_index, label=None, is_uncertain=False):
        """Args:

        parent_index -- the index of the parent token within the document. The dependency will
            always be managed by the parent token, but the index is maintained within the
            object for convenience.
        child_index -- the index of the child token within the document, or one less than the zero
            minus the index of the child token within the document to indicate a grammatical
            dependency. A grammatical dependency means that the parent is replaced by the child
            during matching
        label -- the label of the semantic dependency, which must be *None* for grammatical
            dependencies.
        is_uncertain -- if *True*, a match involving this dependency will itself be uncertain.
        """
        if child_index < 0 and label != None:
            raise RuntimeError(
                'Semantic dependency with negative child index may not have a label.')
        if parent_index == child_index:
            raise RuntimeError(' '.join(
                ('Attempt to create self-referring semantic dependency with index',
                        str(parent_index))))
        self.parent_index = parent_index
        self.child_index = child_index
        self.label = label
        self.is_uncertain = is_uncertain

    def child_token(self, doc):
        """Convenience method to return the child token of this dependency.

        doc -- the document containing the token.
        """
        return doc[self.child_index]

    def __str__(self):
        """e.g. *2:nsubj* or *2:nsubj(U)* to represent uncertainty."""
        working_label = str(self.label)
        if self.is_uncertain:
            working_label = ''.join((working_label, '(U)'))
        return ':'.join((str(self.child_index), working_label))

    def __eq__(self, other):
        return type(other) == SemanticDependency and \
                self.parent_index == other.parent_index and self.child_index == other.child_index

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash((self.parent_index, self.child_index))


class HolmesDictionary:
    """The holder object for token-level semantic information managed by Holmes

    Holmes dictionaries are accessed using the syntax *token._.holmes*.

    index -- the index of the token
    lemma -- the value returned from *_holmes_lemma* for the token.
    children -- list of *SemanticDependency* objects where this token is the parent.
    righthand_siblings -- list of tokens to the right of this token that stand in a conjunction
        relationship to this token and that share its semantic parents.
    """

    def __init__(self, index, lemma):
        self.index = index
        self.lemma = lemma
        self.children = []
        self.righthand_siblings = []
        self.is_involved_in_or_conjunction = False
        self.is_negated = None
        self.is_matchable = None

    @property
    def is_uncertain(self):
        """if *True*, a match involving this token will itself be uncertain."""
        return self.is_involved_in_or_conjunction

    def loop_token_and_righthand_siblings(self, doc):
        """Convenience generator to loop through this token and any righthand siblings."""
        yield doc[self.index]
        for righthand_sibling in self.righthand_siblings:
            yield doc[righthand_sibling]

    def has_dependency_with_child_index(self, index):
        for dependency in self.children:
            if dependency.child_index == index:
                return True
        return False

    def has_dependency_with_label(self, label):
        for dependency in self.children:
            if dependency.label == label:
                return True
        return False

    def has_dependency_with_child_index_and_label(self, index, label):
        for dependency in self.children:
            if dependency.child_index == index and dependency.label == label:
                return True
        return False

    def remove_dependency_with_child_index(self, index):
        self.children = [dep for dep in self.children if dep.child_index != index]

    def string_representation_of_children(self):
        children = sorted(
            self.children, key=lambda dependency: dependency.child_index)
        return '; '.join(str(child) for child in children)


class SerializedHolmesDocument:
    """Consists of the spaCy represention returned by *get_bytes()* plus a jsonpickle representation
        of each token's *SemanticDictionary*.
    """

    def __init__(self, serialized_spacy_document, dictionaries, model):
        self._serialized_spacy_document = serialized_spacy_document
        self._dictionaries = dictionaries
        self._model = model

    def holmes_document(self, semantic_analyzer):
        doc = Doc(semantic_analyzer.nlp.vocab).from_bytes(
            self._serialized_spacy_document)
        for token in doc:
            token._.holmes = self._dictionaries[token.i]
        return doc

class PhraseletTemplate:
    """A template for a phraselet used in topic matching.

    Properties:

    label -- a label for the relation which will be used to form part of the labels of phraselets
        derived from this template.
    template_sentence -- a sentence with the target grammatical structure for phraselets derived
        from this template.
    parent_index -- the index within 'template_sentence' of the parent participant in the dependency
        (for relation phraselets) or of the word (for single-word phraselets).
    child_index -- the index within 'template_sentence' of the child participant in the dependency
        (for relation phraselets) or 'None' for single-word phraselets.
    reverse_matching_dependency_labels -- the labels of dependencies that match the template
        (for relation phraselets) or 'None' for single-word phraselets.
    parent_tags -- the tag_ values of parent participants in the dependency (for parent phraselets)
        of of the word (for single-word phraselets) that match the template. For performance
        reasons, tags that refer to closed word classes like prepositions should be avoided
        where possible.
    child_tags -- the tag_ values of child participants in the dependency (for parent phraselets)
        that match the template, or 'None' for single-word phraselets.
    """

    def __init__(self, label, template_sentence, parent_index, child_index,
            reverse_matching_dependency_labels, parent_tags, child_tags):
        self.label = label
        self.template_sentence = template_sentence
        self.parent_index = parent_index
        self.child_index = child_index
        self.reverse_matching_dependency_labels = reverse_matching_dependency_labels
        self.parent_tags = parent_tags
        self.child_tags = child_tags

    def single_word(self):
        """ 'True' if this is a template for single-word phraselets, otherwise 'False'. """
        return self.child_index == None

class SemanticAnalyzerFactory():
    """Returns the correct *SemanticAnalyzer* for the model language. This class must be added to
        if additional *SemanticAnalyzer* implementations are added for new languages.
    """

    def semantic_analyzer(self, *, model, debug=False):
        language = model[0:2]
        if language == 'en':
            return EnglishSemanticAnalyzer(model=model, debug=debug)
        elif language == 'de':
            return GermanSemanticAnalyzer(model=model, debug=debug)
        else:
            raise ValueError(
                ' '.join(['No semantic analyzer for model', language]))


class SemanticAnalyzer(ABC):
    """Abstract *SemanticAnalyzer* parent class. Functionality is placed here that is common to all
        current implementations. It follows that some functionality will probably have to be moved
        out to specific implementations whenever an implementation for a new language is added.

    For explanations of the abstract variables and methods, see the *EnglishSemanticAnalyzer*
        implementation where they can be illustrated with direct examples.
    """

    def __init__(self, *, model, debug):
        """Args:

        model -- the name of the spaCy model
        debug -- *True* if the object should print a representation of each parsed document
        """
        self.nlp = spacy.load(model)
        self.model = model
        self.debug = debug

    Token.set_extension('holmes', default='')

    def parse(self, text):
        """Performs a full spaCy and Holmes parse on a string.
        """
        spacy_doc = self.spacy_parse(text)
        holmes_doc = self.holmes_parse(spacy_doc)
        return holmes_doc

    _maximum_document_size = 1000000

    def spacy_parse(self, text):
        """Performs a standard spaCy parse on a string.
        """
        if len(text) > self._maximum_document_size:
            raise DocumentTooBigError(' '.join(('size:', str(len(text)), 'max:',
                    str(self._maximum_document_size))))
        return self.nlp(text)

    def holmes_parse(self, spacy_doc):
        """Adds the Holmes-specific information to each token within a spaCy document.
        """
        for token in spacy_doc:
            token._.set('holmes', HolmesDictionary(token.i, self._holmes_lemma(token)))
        for token in spacy_doc:
            self._set_matchability(token)
        for token in spacy_doc:
            self._set_negation(token)
        for token in spacy_doc:
            self._initialize_semantic_dependencies(token)
        for token in spacy_doc:
            self._mark_if_righthand_sibling(token)
        for token in spacy_doc:
            self._copy_any_sibling_info(token)
        for token in spacy_doc:
            self._correct_auxiliaries_and_passives(token)
        for token in spacy_doc:
            self._copy_any_sibling_info(token)
        for token in spacy_doc:
            self._normalize_predicative_adjectives(token)
        for token in spacy_doc:
            self._handle_relative_constructions(token)
        for token in spacy_doc:
            self._create_additional_preposition_phrase_semantic_dependencies(token)
        for token in spacy_doc:
            self._perform_language_specific_tasks(token)
        self.debug_structures(spacy_doc)
        return spacy_doc

    def model_supports_enbeddings(self):
        return self.nlp.meta['vectors']['vectors'] > 0

    def model_supports_coreference_resolution(self):
        return self.nlp.has_pipe('neuralcoref')

    def dependency_labels_match(self, *, search_phrase_dependency_label, document_dependency_label):
        """Determines whether a dependency label in a search phrase matches a dependency label in
            a document being searched.
        """
        if search_phrase_dependency_label == document_dependency_label:
            return True
        if search_phrase_dependency_label not in self._matching_dep_dict.keys():
            return False
        return document_dependency_label in self._matching_dep_dict[search_phrase_dependency_label]

    def debug_structures(self, doc):
        if self.debug:
            for token in doc:
                negation_string = 'negative' if token._.holmes.is_negated else 'positive'
                uncertainty_string = 'uncertain' if token._.holmes.is_uncertain else 'certain'
                matchability_string = 'matchable' if token._.holmes.is_matchable else 'unmatchable'
                if self.is_involved_in_coreference(token):
                    coreference_string = token._.coref_clusters
                else:
                    coreference_string = ''
                print(token.i, token.text, token._.holmes.lemma, token.pos_, token.tag_,
                        token.dep_, token.ent_type_, token.head.i,
                        token._.holmes.string_representation_of_children(),
                        token._.holmes.righthand_siblings, negation_string,
                        uncertainty_string, matchability_string, coreference_string)

    def to_serialized_string(self, spacy_doc):
        dictionaries = []
        for token in spacy_doc:
            dictionaries.append(token._.holmes)
            token._.holmes = None
        serialized_document = SerializedHolmesDocument(
            spacy_doc.to_bytes(), dictionaries, self.model)
        for token in spacy_doc:
            token._.holmes = dictionaries[token.i]
        return jsonpickle.encode(serialized_document)

    def from_serialized_string(self, serialized_spacy_doc):
        serialized_document = jsonpickle.decode(serialized_spacy_doc)
        if serialized_document._model != self.model:
            raise WrongModelDeserializationError(serialized_document._model)
        return serialized_document.holmes_document(self)

    def get_dependent_phrase(self, token):
        "Return the dependent phrase of a token. Used in building match dictionaries"
        if not token.pos_ in self.noun_pos:
            return token.text
        return_string = ''
        pointer = token.left_edge.i - 1
        while True:
            pointer += 1
            if token.doc[pointer].pos_ not in self.noun_pos and token.doc[pointer].dep_ not in \
                    self.noun_kernel_dep and pointer > token.i:
                return return_string.strip()
            if return_string == '':
                return_string = token.doc[pointer].text
            else:
                return_string = ' '.join((return_string, token.doc[pointer].text))
            if (token.right_edge.i <= pointer):
                return return_string

    def is_involved_in_coreference(self, token):
        if self.model_supports_coreference_resolution() and token.doc._.has_coref:
            for cluster in token._.coref_clusters:
                for mention in cluster.mentions:
                    if mention.root.i == token.i or token.i in \
                            mention.root._.holmes.righthand_siblings:
                        return True
        return False

    def token_and_coreference_chain_indexes(self, token):
        """Return the indexes of the token itself and any tokens with which it is linked by
            coreference chains up to the maximum number of mentions away.
        """
        if not self.is_involved_in_coreference(token):
            list_to_return = [token.i]
        else:
            list_to_return = []
            # find out which cluster *token* is in
            for cluster in token._.coref_clusters:
                counter = 0
                for span in cluster:
                    for candidate in span.root._.holmes.loop_token_and_righthand_siblings(
                            token.doc):
                        if candidate.i == token.i:
                            token_mention_index = counter
                    counter += 1
            for cluster in token._.coref_clusters:
                counter = 0
                for span in cluster:
                    if abs(counter - token_mention_index) <= \
                            self._maximum_mentions_in_coreference_chain:
                        for candidate in span.root._.holmes.loop_token_and_righthand_siblings(
                                token.doc):
                            if candidate.i >= span.start and candidate.i <= span.end and not \
                                    (token.i >= span.start and token.i <= span.end and
                                    candidate.i != token.i):
                                list_to_return.append(candidate.i)
                    counter += 1
        return list_to_return

    language_name = NotImplemented

    noun_pos = NotImplemented

    _matchable_pos = NotImplemented

    _adjectival_predicate_head_pos = NotImplemented

    _adjectival_predicate_subject_pos = NotImplemented

    noun_kernel_dep = NotImplemented

    sibling_marker_deps = NotImplemented

    _adjectival_predicate_subject_dep = NotImplemented

    _adjectival_predicate_predicate_dep = NotImplemented

    _modifier_dep = NotImplemented

    _spacy_noun_to_preposition_dep = NotImplemented

    _spacy_verb_to_preposition_dep = NotImplemented

    _holmes_noun_to_preposition_dep = NotImplemented

    _holmes_verb_to_preposition_dep = NotImplemented

    _conjunction_deps = NotImplemented

    _interrogative_pronoun_tags = NotImplemented

    _semantic_dependency_excluded_tags = NotImplemented

    _generic_pronoun_lemmas = NotImplemented

    _or_lemma = NotImplemented

    _matching_dep_dict = NotImplemented

    _mark_child_dependencies_copied_to_siblings_as_uncertain = NotImplemented

    _maximum_mentions_in_coreference_chain = NotImplemented

    phraselet_templates = NotImplemented

    phraselet_stop_lemmas = NotImplemented

    @abstractmethod
    def _set_negation(self, token):
        pass

    @abstractmethod
    def _correct_auxiliaries_and_passives(self, token):
        pass

    @abstractmethod
    def _perform_language_specific_tasks(self, token):
        pass

    @abstractmethod
    def _handle_relative_constructions(self, token):
        pass

    @abstractmethod
    def _holmes_lemma(self, token):
        pass

    def _initialize_semantic_dependencies(self, token):
        for child in (child for child in token.children if child.dep_ != 'punct' and child.tag_
                not in self._semantic_dependency_excluded_tags):
            token._.holmes.children.append(SemanticDependency(token.i, child.i, child.dep_))

    def _mark_if_righthand_sibling(self, token):
        if token.dep_ in self.sibling_marker_deps:  # i.e. is righthand sibling
            working_token = token
            working_or_conjunction_flag = False
            # work up through the tree until the lefthandmost sibling element with the
            # semantic relationships to the rest of the sentence is reached
            while working_token.dep_ in self._conjunction_deps:
                working_token = working_token.head
                for working_child in working_token.children:
                    if working_child.lemma_ == self._or_lemma:
                        working_or_conjunction_flag = True
            # add this element to the lefthandmost sibling as a righthand sibling
            working_token._.holmes.righthand_siblings.append(token.i)
            if working_or_conjunction_flag:
                working_token._.holmes.is_involved_in_or_conjunction = True

    def _copy_any_sibling_info(self, token):
        # Copy the or conjunction flag to righthand siblings
        if token._.holmes.is_involved_in_or_conjunction:
            for righthand_sibling in token._.holmes.righthand_siblings:
                token.doc[righthand_sibling]._.holmes.is_involved_in_or_conjunction = True
        for dependency in token._.holmes.children.copy():
            # where a token has a dependent token and the dependent token has righthand siblings,
            # add dependencies from the parent token to the siblings
            for child_righthand_sibling in \
                    token.doc[dependency.child_index]._.holmes.righthand_siblings:
                # Check this token does not already have the dependency
                if len([dependency for dependency in token._.holmes.children if
                        dependency.child_index == child_righthand_sibling]) == 0:
                    child_index_to_add = child_righthand_sibling
                    # If this token is a grammatical element, it needs to point to new
                    # child dependencies as a grammatical element as well
                    if dependency.child_index < 0:
                        child_index_to_add = 0 - (child_index_to_add + 1)
                    # Check adding the new dependency will not result in a loop and that
                    # this token still does not have the dependency now its index has
                    # possibly been changed
                    if token.i != child_index_to_add and not \
                            token._.holmes.has_dependency_with_child_index(child_index_to_add):
                        token._.holmes.children.append(SemanticDependency(
                            token.i, child_index_to_add, dependency.label, dependency.is_uncertain))
            # where a token has a dependent token and the parent token has righthand siblings,
            # add dependencies from the siblings to the dependent token
            for righthand_sibling in [righthand_sibling for righthand_sibling in \
             token._.holmes.righthand_siblings if righthand_sibling != dependency.child_index]:
                # unless the sibling already contains a dependency with the same label
                # or the sibling has this token as a dependent child
                righthand_sibling_token = token.doc[righthand_sibling]
                if len([sibling_dependency for sibling_dependency in
                        righthand_sibling_token._.holmes.children if
                        sibling_dependency.label == dependency.label]) == 0 and \
                        dependency.label not in self._conjunction_deps and not \
                        righthand_sibling_token._.holmes.has_dependency_with_child_index(
                        dependency.child_index) and righthand_sibling != \
                        dependency.child_index:
                    righthand_sibling_token._.holmes.children.append(SemanticDependency(
                        righthand_sibling, dependency.child_index, dependency.label,
                        self._mark_child_dependencies_copied_to_siblings_as_uncertain))

    def _normalize_predicative_adjectives(self, token):
        """Change phrases like *the town is old* and *the man is poor* so their
            semantic structure is equivalent to *the old town* and *the poor man*.
        """
        if token.pos_ == self._adjectival_predicate_head_pos:
            altered = False
            for predicative_adjective_index in (dependency.child_index for dependency in \
                    token._.holmes.children if dependency.label ==
                    self._adjectival_predicate_predicate_dep and
                    token.doc[dependency.child_index].pos_ == 'ADJ' and
                    dependency.child_index >= 0):
                for subject_index in (dependency.child_index for dependency in \
                        token._.holmes.children if dependency.label ==
                        self._adjectival_predicate_subject_dep and
                        (dependency.child_token(token.doc).pos_ in
                        self._adjectival_predicate_subject_pos or
                        self.is_involved_in_coreference(dependency.child_token(token.doc))) and
                        dependency.child_index >= 0 and \
                        dependency.child_index != predicative_adjective_index):
                    token.doc[subject_index]._.holmes.children.append(
                            SemanticDependency(subject_index, predicative_adjective_index,
                            self._modifier_dep))
                    altered = True
            if altered:
                token._.holmes.children = [SemanticDependency(
                        token.i, 0 - (subject_index + 1), None)]

    def _create_additional_preposition_phrase_semantic_dependencies(self, token):
        """In structures like 'Somebody needs insurance for a period' it seems to be
            mainly language-dependent whether the preposition phrase is analysed as being
            dependent on the preceding noun or the preceding verb. We add an additional, new
            dependency to whichever of the noun or the verb does not already have one. The new
            label is defined in *_matching_dep_dict* in such a way that original dependencies
            in search phrases match new dependencies in documents but not vice versa.
        """

        def add_dependencies_pointing_to_preposition_and_siblings(parent, label):
            for working_preposition in token._.holmes.loop_token_and_righthand_siblings(token.doc):
                if parent.i != working_preposition.i:
                    parent._.holmes.children.append(SemanticDependency(parent.i,
                            working_preposition.i, label, True))

        # token is a preposition ...
        if token.pos_ == 'ADP':
            # directly preceded by a noun
            if token.i > 0 and token.doc[token.i-1].sent == token.sent and \
                    (token.doc[token.i-1].pos_ in ('NOUN', 'PROPN') or
                    self.is_involved_in_coreference(token.doc[token.i-1])):
                preceding_noun = token.doc[token.i-1]
                # and the noun is governed by at least one verb
                governing_verbs = [working_token for working_token in token.sent
                        if working_token.i < token.i and working_token.pos_ == 'VERB' and
                        working_token._.holmes.has_dependency_with_child_index(
                        preceding_noun.i)]
                if len(governing_verbs) == 0:
                    return
                # if the noun governs the preposition, add new possible dependencies
                # from the verb(s)
                for governing_verb in governing_verbs:
                    if preceding_noun._.holmes.has_dependency_with_child_index_and_label(
                            token.i, self._spacy_noun_to_preposition_dep) and not \
                            governing_verb._.holmes.has_dependency_with_child_index_and_label(
                            token.i, self._spacy_verb_to_preposition_dep):
                        add_dependencies_pointing_to_preposition_and_siblings(governing_verb,
                                self._holmes_verb_to_preposition_dep)
                # if the verb(s) governs the preposition, add new possible dependencies
                # from the noun
                if governing_verbs[0]._.holmes.has_dependency_with_child_index_and_label(
                        token.i, self._spacy_verb_to_preposition_dep) and not \
                        preceding_noun._.holmes.has_dependency_with_child_index_and_label(
                        token.i, self._spacy_noun_to_preposition_dep):
                    # check the preposition is not pointing back to a relative clause
                    for preposition_dep_index in (dep.child_index for dep in
                            token._.holmes.children):
                        if token.doc[preposition_dep_index]._.holmes.\
                                has_dependency_with_label('relcl'):
                            return
                    add_dependencies_pointing_to_preposition_and_siblings(preceding_noun,
                            self._holmes_noun_to_preposition_dep)

    def _set_matchability(self, token):
        """Marks whether this token, if it appears in a search phrase, should require a counterpart
        in a document being matched.
        """
        token._.holmes.is_matchable = (token.pos_ in self._matchable_pos or
                self.is_involved_in_coreference(token)) and \
                token.tag_ not in self._interrogative_pronoun_tags and \
                token._.holmes.lemma not in self._generic_pronoun_lemmas

    def _move_information_between_tokens(self, from_token, to_token):
        """Moves semantic child and sibling information from one token to another.

        Args:

        from_token -- the source token, which will be marked as a grammatical token
        pointing to *to_token*.
        to_token -- the destination token.
        """
        linking_dependencies = [dependency for dependency in from_token._.holmes.children
                if dependency.child_index == to_token.i]
        if len(linking_dependencies) == 0:
            return  # only happens if there is a problem with the spaCy structure
        linking_dependency_label = linking_dependencies[0].label
        # only loop dependencies whose label or index are not already present at the destination
        for dependency in (dependency for dependency in from_token._.holmes.children
                if dependency.label != linking_dependency_label and not
                to_token._.holmes.has_dependency_with_child_index(dependency.child_index) and
                to_token.i != dependency.child_index):
            to_token._.holmes.children.append(SemanticDependency(
                to_token.i, dependency.child_index, dependency.label, dependency.is_uncertain))
        from_token._.holmes.children = [SemanticDependency(from_token.i, 0 - (to_token.i + 1))]
        to_token._.holmes.righthand_siblings.extend(
            from_token._.holmes.righthand_siblings)
        if from_token._.holmes.is_involved_in_or_conjunction:
            to_token._.holmes.is_involved_in_or_conjunction = True
        if from_token._.holmes.is_negated:
            to_token._.holmes.is_negated = True
        # If from_token is the righthand sibling of some other token within the same sentence,
        # replace that token's reference with a reference to to_token
        for token in from_token.sent:
            if from_token.i in token._.holmes.righthand_siblings:
                token._.holmes.righthand_siblings.remove(from_token.i)
                if token.i != to_token.i:
                    token._.holmes.righthand_siblings.append(to_token.i)

class EnglishSemanticAnalyzer(SemanticAnalyzer):

    language_name = 'English'

    # The part of speech tags that require a match in the search sentence when they occur within a
    # search_phrase
    _matchable_pos = ('ADJ', 'ADP', 'ADV', 'NOUN', 'NUM', 'PROPN', 'VERB')

    # The part of speech tags that can refer to nouns
    noun_pos = ('NOUN', 'PROPN')

    # The part of speech tags that can refer to the head of an adjectival predicate phrase
    # ("is" in "The dog is tired")
    _adjectival_predicate_head_pos = 'VERB'

    # The part of speech tags that can refer to the subject of a adjectival predicate
    # ("dog" in "The dog is tired")
    _adjectival_predicate_subject_pos = ('NOUN', 'PROPN')

    # Dependency labels that mark noun kernel elements that are not the head noun
    noun_kernel_dep = ('nmod', 'compound', 'appos', 'nummod')

    # Dependency labels that can mark righthand siblings
    sibling_marker_deps = ('conj', 'appos')

    # Dependency label that marks the subject of a adjectival predicate
    _adjectival_predicate_subject_dep = 'nsubj'

    # Dependency label that marks the predicate of a adjectival predicate
    _adjectival_predicate_predicate_dep = 'acomp'

    # Dependency label that marks a modifying adjective
    _modifier_dep = 'amod'

    # Original dependency label from nouns to prepositions
    _spacy_noun_to_preposition_dep = 'prep'

    # Original dependency label from verbs to prepositions
    _spacy_verb_to_preposition_dep = 'prep'

    # Added possible dependency label from nouns to prepositions
    _holmes_noun_to_preposition_dep = 'prepposs'

    # Added possible dependency label from verbs to prepositions
    _holmes_verb_to_preposition_dep = 'prepposs'

    # Dependency labels that occur in a conjunction phrase (righthand siblings and conjunctions)
    _conjunction_deps = ('conj', 'appos', 'cc')

    # Syntactic tags that can mark interrogative pronouns
    _interrogative_pronoun_tags = ('WDT', 'WP', 'WRB')

    # Syntactic tags that exclude a token from being the child token within a semantic dependency
    _semantic_dependency_excluded_tags = ('DT')

    # Generic pronouns
    _generic_pronoun_lemmas = ('something', 'somebody', 'someone')

    # The word for 'or' in this language
    _or_lemma = 'or'

    # Map from dependency tags as occurring within search phrases to corresponding dependency tags
    # as occurring within documents being searched. This is the main source of the asymmetry
    # in matching from search phrases to documents versus from documents to search phrases.
    _matching_dep_dict = {
            'nsubj': ['csubj', 'poss', 'pobjb', 'advmodsubj'],
            'acomp': ['amod', 'advmod', 'npmod'],
            'amod': ['acomp', 'advmod', 'npmod'],
            'advmod': ['acomp', 'amod', 'npmod'],
            'dative': ['pobjt', 'relant', 'nsubjpass'],
            'pobjt': ['dative', 'relant'],
            'nsubjpass': ['dobj', 'pobjo', 'poss', 'relant', 'csubjpass', 'compound', 'advmodobj'],
             'dobj': ['pobjo', 'poss', 'relant', 'nsubjpass', 'csubjpass', 'compound','advmodobj'],
             'nmod': ['appos', 'compound', 'nummod'],
             'poss': ['pobjo'],
             'pobjo': ['poss'],
             'pobjb': ['nsubj', 'csubj', 'poss', 'advmodsubj'],
             'prep': ['prepposs']
             }

    # Where dependencies from a parent to a child are copied to the parent's righthand siblings,
    # it can make sense to mark the dependency as uncertain depending on the underlying spaCy
    # representations for the individual language
    _mark_child_dependencies_copied_to_siblings_as_uncertain = True

    # Coreference chains are only processed up to this number of mentions away from the currently
    # matched document location
    _maximum_mentions_in_coreference_chain = 3

    # The templates used to generate topic matching phraselets.
    phraselet_templates = [
        PhraseletTemplate("predicate-actor", "A thing does", 2, 1,
                ['nsubj', 'csubj', 'pobjb', 'advmodsubj'],
                ['FW', 'NN', 'NNP', 'NNPS', 'NNS', 'VB', 'VBD', 'VBG', 'VBN', 'VBP', 'VBZ'],
                ['FW', 'NN', 'NNP', 'NNPS', 'NNS']),
        PhraseletTemplate("predicate-patient", "Somebody does a thing", 1, 3,
                ['dobj', 'relant', 'nsubjpass', 'csubjpass','advmodobj', 'pobjo'],
                ['FW', 'NN', 'NNP', 'NNPS', 'NNS', 'VB', 'VBD', 'VBG', 'VBN', 'VBP', 'VBZ'],
                ['FW', 'NN', 'NNP', 'NNPS', 'NNS']),
        PhraseletTemplate("predicate-recipient", "Somebody gives a thing something", 1, 3,
                ['dative', 'pobjt'],
                ['FW', 'NN', 'NNP', 'NNPS', 'NNS', 'VB', 'VBD', 'VBG', 'VBN', 'VBP', 'VBZ'],
                ['FW', 'NN', 'NNP', 'NNPS', 'NNS']),
        PhraseletTemplate("governor-adjective", "A described thing", 2, 1,
                ['acomp', 'amod', 'advmod', 'npmod', 'advcl'],
                ['FW', 'NN', 'NNP', 'NNPS', 'NNS', 'VB', 'VBD', 'VBG', 'VBN', 'VBP', 'VBZ'],
                ['JJ', 'JJR', 'JJS', 'VBN', 'RB', 'RBR', 'RBS']),
        PhraseletTemplate("noun-noun", "A thing thing", 2, 1,
                ['nmod', 'appos', 'compound', 'nunmod'],
                ['FW', 'NN', 'NNP', 'NNPS', 'NNS'],
                ['FW', 'NN', 'NNP', 'NNPS', 'NNS']),
        PhraseletTemplate("possessor-possessed", "A thing's thing", 3, 1,
                ['poss', 'pobjo'],
                ['FW', 'NN', 'NNP', 'NNPS', 'NNS'],
                ['FW', 'NN', 'NNP', 'NNPS', 'NNS']),
        PhraseletTemplate("word", "thing", 0, None,
                None,
                ['FW', 'NN', 'NNP', 'NNPS', 'NNS'],
                None)
                ]

    # Lemmas that should be suppressed as parents within relation phraselets or as words of
    # single-word phraselets.
    phraselet_stop_lemmas = ['be', 'have']

    def _set_negation(self, token):
        """Marks the negation on the token. A token is negative if it or one of its ancestors
            has a negation word as a syntactic (not semantic!) child.
        """
        if token._.holmes.is_negated != None:
            return
        for child in token.children:
            if child._.holmes.lemma in ('nobody', 'nothing', 'nowhere', 'noone', 'neither',
                    'nor', 'no') or child.dep_ == 'neg':
                token._.holmes.is_negated = True
                return
            if child._.holmes.lemma in ('more', 'longer'):
                for grandchild in child.children:
                    if grandchild._.holmes.lemma == 'no':
                        token._.holmes.is_negated = True
                        return
        if token.dep_ == 'ROOT':
            token._.holmes.is_negated = False
            return
        self._set_negation(token.head)
        token._.holmes.is_negated = token.head._.holmes.is_negated

    def _correct_auxiliaries_and_passives(self, token):
        """Wherever auxiliaries and passives are found, derive the semantic information
            from the syntactic information supplied by spaCy.
        """
        # 'auxpass' means an auxiliary used in a passive context. We mark its subject with
        # a new dependency label 'nsubjpass' that matches objects.
        if len([dependency for dependency in token._.holmes.children
                if dependency.label == 'auxpass']) > 0:
            for dependency in token._.holmes.children:
                if dependency.label == 'nsubj':
                    dependency.label = 'nsubjpass'

        # Structures like 'he used to' and 'he is going to'
        for dependency in (dependency for dependency in token._.holmes.children
                if dependency.label == 'xcomp'):
            child = dependency.child_token(token.doc)
            # distinguish 'he used to ...' from 'he used it to ...'
            if token._.holmes.lemma == 'use' and token.tag_ == 'VBD' and \
                    len([element for element in token._.holmes.children
                    if element.label == 'dobj']) == 0:
                self._move_information_between_tokens(token, child)
            elif token._.holmes.lemma == 'go':
                # 'was going to' is marked as uncertain, 'is going to' is not marked as uncertain
                uncertainty_flag = False
                for other_dependency in (other_dependency for other_dependency in
                        token._.holmes.children if other_dependency.label == 'aux'):
                    other_dependency_token = other_dependency.child_token(token.doc)
                    if other_dependency_token._.holmes.lemma == 'be' and \
                            other_dependency_token.tag_ == 'VBD':  # 'was going to'
                        uncertainty_flag = True
                self._move_information_between_tokens(token, child)
                if uncertainty_flag:
                    for child_dependency in child._.holmes.children:
                        child_dependency.is_uncertain = True
            else:
                # constructions like:
                #
                #'she told him to close the contract'
                #'he decided to close the contract'
                for other_dependency in token._.holmes.children:
                    if other_dependency.label in ('dobj', 'nsubjpass') or \
                    (other_dependency.label == 'nsubj' and \
                    len([element for element in token._.holmes.children
                    if element.label == 'dobj']) == 0):
                        if len([element for element in child._.holmes.children
                        if element.label == 'auxpass']) > 0:
                            if not child._.holmes.has_dependency_with_child_index(
                                    other_dependency.child_index) and \
                                    dependency.child_index != other_dependency.child_index:
                                child._.holmes.children.append(SemanticDependency(
                                    dependency.child_index, other_dependency.child_index,
                                    'nsubjpass', True))
                        else:
                            if not child._.holmes.has_dependency_with_child_index(
                                    other_dependency.child_index) and \
                                    dependency.child_index != other_dependency.child_index:
                                child._.holmes.children.append(SemanticDependency(
                                    dependency.child_index, other_dependency.child_index,
                                    'nsubj', True))

    def _lefthand_sibling_recursively(self, token):
        """If *token* is a righthand sibling, return the token that has a sibling reference
            to it, otherwise return *token* itself.
        """
        if token.dep_ not in self._conjunction_deps:
            return token
        else:
            return self._lefthand_sibling_recursively(token.head)

    def _handle_relative_constructions(self, token):
        """Wherever auxiliaries and passives are found, derive the semantic information
            from the syntactic information supplied by spaCy.
        """

        if token.dep_ == 'relcl':
            for dependency in token._.holmes.children:
                child = dependency.child_token(token.doc)
                # handle 'whose' clauses
                for child_dependency in (child_dependency for child_dependency in
                        child._.holmes.children if child_dependency.label == 'poss' and
                        child_dependency.child_token(token.doc).tag_ == 'WP$'):
                    whose_pronoun_token = child_dependency.child_token(
                            token.doc)
                    working_index = whose_pronoun_token.i
                    while working_index >= token.sent.start:
                        # find the antecedent (possessed entity)
                        for dependency in (dependency for dependency in
                                whose_pronoun_token.doc[working_index]._.holmes.children
                                if dependency.label == 'relcl'):
                            working_token = child.doc[working_index]
                            working_token = self._lefthand_sibling_recursively(working_token)
                            for lefthand_sibling_of_antecedent in \
                                    working_token._.holmes.loop_token_and_righthand_siblings(
                                            token.doc):
                                # find the possessing noun
                                for possessing_noun in (possessing_noun for possessing_noun in
                                        child._.holmes.loop_token_and_righthand_siblings(token.doc)
                                        if possessing_noun.i != lefthand_sibling_of_antecedent.i):
                                    # add the semantic dependency
                                    possessing_noun._.holmes.children.append(
                                            SemanticDependency(possessing_noun.i,
                                            lefthand_sibling_of_antecedent.i, 'poss',
                                            lefthand_sibling_of_antecedent.i != working_index))
                                    # remove the syntactic dependency
                                    possessing_noun._.holmes.remove_dependency_with_child_index(
                                            whose_pronoun_token.i)
                                whose_pronoun_token._.holmes.children = [SemanticDependency(
                                        whose_pronoun_token.i, 0 - (working_index + 1), None)]
                            return
                        working_index -= 1
                    return
                if child.tag_ in ('WP', 'WRB', 'WDT', 'IN'):  # 'that' or 'which'
                    working_dependency_label = dependency.label
                    child._.holmes.children = [SemanticDependency(child.i, 0 - (token.head.i + 1),
                            None)]
                else:
                    # relative antecedent, new dependency tag, 'the man I saw yesterday'
                    working_dependency_label = 'relant'
                last_righthand_sibling_of_predicate = list(
                        token._.holmes.loop_token_and_righthand_siblings(token.doc))[-1]
                for preposition_dependency in (dep for dep in
                        last_righthand_sibling_of_predicate._.holmes.children if dep.label=='prep'
                        and dep.child_token(token.doc)._.holmes.is_matchable):
                    preposition = preposition_dependency.child_token(token.doc)
                    for grandchild_dependency in (dep for dep in
                            preposition._.holmes.children if
                            dep.child_token(token.doc).tag_ in ('WP', 'WRB', 'WDT', 'IN')
                            and dep.child_token(token.doc).i > 0):
                            # 'that' or 'which'
                        complementizer = grandchild_dependency.child_token(token.doc)
                        preposition._.holmes.remove_dependency_with_child_index(
                                grandchild_dependency.child_index)
                        # a new relation pointing directly to the antecedent noun
                        # will be added in the section below
                        complementizer._.holmes.children = \
                                [SemanticDependency(grandchild_dependency.child_index,
                                0-(grandchild_dependency.child_index + 1), None)]
                displaced_preposition_dependencies = [dep for dep in
                        last_righthand_sibling_of_predicate._.holmes.children if dep.label=='prep'
                        and len(dep.child_token(token.doc)._.holmes.children) == 0
                        and dep.child_token(token.doc)._.holmes.is_matchable]
                antecedent = self._lefthand_sibling_recursively(token.head)
                if len(displaced_preposition_dependencies) > 0:
                    displaced_preposition = \
                            displaced_preposition_dependencies[0].child_token(token.doc)
                    for lefthand_sibling_of_antecedent in (lefthand_sibling_of_antecedent for
                            lefthand_sibling_of_antecedent in
                            antecedent._.holmes.loop_token_and_righthand_siblings(token.doc)
                            if displaced_preposition.i != lefthand_sibling_of_antecedent.i):
                        displaced_preposition._.holmes.children.append(SemanticDependency(
                                displaced_preposition.i, lefthand_sibling_of_antecedent.i,
                                'pobj',
                                lefthand_sibling_of_antecedent.i != token.head.i))
                        #Where the antecedent is not the final one before the relative
                        #clause, mark the dependency as uncertain
                    for sibling_of_pred in \
                            token._.holmes.loop_token_and_righthand_siblings(token.doc):
                        if not sibling_of_pred._.holmes.has_dependency_with_child_index(
                                displaced_preposition.i) and sibling_of_pred.i != \
                                displaced_preposition.i:
                            sibling_of_pred._.holmes.children.append(SemanticDependency(
                                sibling_of_pred.i, displaced_preposition.i, 'prep', True))
                        if working_dependency_label != 'relant':
                        # if 'that' or 'which', remove it
                            sibling_of_pred._.holmes.remove_dependency_with_child_index(
                                    child.i)
                else:
                    for lefthand_sibling_of_antecedent in \
                            antecedent._.holmes.loop_token_and_righthand_siblings(token.doc):
                        for sibling_of_predicate in (sibling_of_predicate for sibling_of_predicate in token._.holmes.loop_token_and_righthand_siblings(token.doc)
                                if sibling_of_predicate.i != lefthand_sibling_of_antecedent.i):
                            sibling_of_predicate._.holmes.children.append(SemanticDependency(
                                    sibling_of_predicate.i, lefthand_sibling_of_antecedent.i,
                                    working_dependency_label,
                                    lefthand_sibling_of_antecedent.i != token.head.i))
                            #Where the antecedent is not the final one before the relative
                            #clause, mark the dependency as uncertain
                            if working_dependency_label != 'relant':
                                sibling_of_predicate._.holmes.remove_dependency_with_child_index(
                                        child.i)
                break

    def _holmes_lemma(self, token):
        """Relabel the lemmas of phrasal verbs in sentences like 'he gets up' to incorporate
            the entire phrasal verb to facilitate matching.
        """
        if token.pos_ == 'VERB':
            for child in token.children:
                if child.tag_ == 'RP':
                    return ' '.join([token.lemma_.lower(), child.lemma_.lower()])
        return token.lemma_.lower()

    def _perform_language_specific_tasks(self, token):

        # Because phrasal verbs are conflated into a single lemma, remove the dependency
        # from the verb to the preposition
        if token.tag_ == 'RP':
            token.head._.holmes.remove_dependency_with_child_index(token.i)

        # mark modal verb dependencies as uncertain
        if token.pos_ == 'VERB':
            for dependency in (dependency for dependency in token._.holmes.children
                    if dependency.label == 'aux'):
                child = dependency.child_token(token.doc)
                if child.pos_ == 'VERB' and child._.holmes.lemma not in \
                        ('be', 'have', 'do', 'go', 'use', 'will', 'shall'):
                    for other_dependency in [other_dependency for other_dependency in
                            token._.holmes.children if other_dependency.label != 'aux']:
                        other_dependency.is_uncertain = True

        # set auxiliaries as not matchable
        if token.dep_ in ('aux', 'auxpass'):
            token._.holmes.is_matchable = False

        # Add new dependencies to phrases with 'by', 'of' and 'to' to enable the matching
        # of deverbal nominal phrases with verb phrases, also add 'dative' dependency to
        # nouns within dative 'to' phrases
        for dependency in (dependency for dependency in token._.holmes.children
                if dependency.label in ('prep', 'agent', 'dative')):
            child = dependency.child_token(token.doc)
            if child._.holmes.lemma == 'by':
                working_dependency_label = 'pobjb'
            elif child._.holmes.lemma == 'of':
                working_dependency_label = 'pobjo'
            elif child._.holmes.lemma == 'to':
                if dependency.label == 'dative':
                    working_dependency_label = 'dative'
                else:
                    working_dependency_label = 'pobjt'
            else:
                continue
            # the actual preposition is marked as not matchable
            child._.holmes.is_matchable = False
            for child_dependency in (child_dependency for child_dependency in
                    child._.holmes.children if child_dependency.label == 'pobj' if token.i !=
                    child_dependency.child_index):
                token._.holmes.children.append(
                        SemanticDependency(token.i, child_dependency.child_index,
                        working_dependency_label))

        # handle past passive participles
        if token.dep_ == 'acl' and token.tag_ == 'VBN':
            lefthand_sibling = self._lefthand_sibling_recursively(token.head)
            for antecedent in \
                    lefthand_sibling._.holmes.loop_token_and_righthand_siblings(token.doc):
                if token.i != antecedent.i:
                    token._.holmes.children.append(
                        SemanticDependency(token.i, antecedent.i, 'dobj'))

        # handle phrases like 'cat-eating dog' and 'dog-eaten cat', adding new dependencies
        if token.dep_ == 'amod' and token.pos_ == 'VERB':
            for dependency in (dependency for dependency in token._.holmes.children
                    if dependency.label == 'npadvmod'):
                if token.tag_ == 'VBG':
                    dependency.label = 'advmodobj'
                    noun_dependency = 'advmodsubj'
                elif token.tag_ == 'VBN':
                    dependency.label = 'advmodsubj'
                    noun_dependency = 'advmodobj'
                else:
                    break
                for noun in token.head._.holmes.loop_token_and_righthand_siblings(token.doc):
                    if token.i != noun.i:
                        token._.holmes.children.append(SemanticDependency(
                            token.i, noun.i, noun_dependency, noun.i != token.head.i))
                break  # we only handle one antecedent, spaCy never seems to produce more anyway

        # handle phrases like 'he is thinking about singing', 'he keeps on singing'
        # find governed verb
        if token.pos_ == 'VERB' and token.dep_ == 'pcomp':
            # choose correct noun dependency for passive or active structure
            if len([dependency for dependency in token._.holmes.children
                    if dependency.label == 'auxpass']) > 0:
                new_dependency_label = 'nsubjpass'
            else:
                new_dependency_label = 'nsubj'
            # check that governed verb does not already have a dependency with the same label
            if len([target_token_dependency for target_token_dependency in token._.holmes.children
                    if target_token_dependency.label == new_dependency_label]) == 0:
                # Go back in the sentence to find the first subject phrase
                counter = token.i
                while True:
                    counter -= 1
                    if counter < token.sent.start:
                        return
                    if token.doc[counter].dep_ in ('nsubj', 'nsubjpass'):
                        break
                # From the subject phrase loop up through the syntactic parents
                # to find the governing verb
                working_token = token.doc[counter]
                while True:
                    if working_token.tag_.startswith('NN') or \
                            self.is_involved_in_coreference(working_token):
                        for source_token in \
                                working_token._.holmes.loop_token_and_righthand_siblings(token.doc):
                            for target_token in \
                                    token._.holmes.loop_token_and_righthand_siblings(token.doc):
                                if target_token.i != source_token.i:
                                    # such dependencies are always uncertain
                                    target_token._.holmes.children.append(SemanticDependency(
                                            target_token.i, source_token.i, new_dependency_label,
                                            True))
                        return
                    if working_token.dep_ != 'ROOT':
                        working_token = working_token.head
                    else:
                        return


class GermanSemanticAnalyzer(SemanticAnalyzer):

    language_name = 'German'

    noun_pos = ('NOUN', 'PROPN', 'ADJ')

    _matchable_pos = ('ADJ', 'ADP', 'ADV', 'NOUN', 'NUM', 'PROPN', 'VERB', 'AUX')

    _adjectival_predicate_head_pos = 'AUX'

    _adjectival_predicate_subject_pos = ('NOUN', 'PROPN', 'PRON')

    noun_kernel_dep = ('nk', 'pnc')

    sibling_marker_deps = ('cj', 'app')

    _adjectival_predicate_subject_dep = 'sb'

    _adjectival_predicate_predicate_dep = 'pd'

    _modifier_dep = 'nk'

    _spacy_noun_to_preposition_dep = 'mnr'

    _spacy_verb_to_preposition_dep = 'mo'

    _holmes_noun_to_preposition_dep = 'mnrposs'

    _holmes_verb_to_preposition_dep = 'moposs'

    _conjunction_deps = ('cj', 'cd', 'punct', 'app')

    _interrogative_pronoun_tags = ('PWAT', 'PWAV', 'PWS')

    _semantic_dependency_excluded_tags = ('ART')

    _generic_pronoun_lemmas = ('jemand', 'etwas')

    _or_lemma = 'oder'

    _matching_dep_dict = {
            'sb': ['nk', 'ag', 'mnr'],
            'ag': ['nk', 'mnr'],
            'da': ['nk', 'og', 'op'],
            'oa': ['nk', 'og', 'op', 'ag', 'mnr'],
            'og': ['oa', 'da', 'nk', 'op'],
            'nk': ['ag'],
            'mo': ['moposs', 'op'],
            'mnr': ['mnrposs', 'op']
            }

    _mark_child_dependencies_copied_to_siblings_as_uncertain = False

    # Never used at the time of writing
    _maximum_mentions_in_coreference_chain = 3

    phraselet_templates = [
        PhraseletTemplate("verb-nom", "Eine Sache tut", 2, 1,
                ['sb'],
                ['VMFIN', 'VMINF', 'VMPP', 'VVFIN', 'VVIMP', 'VVINF', 'VVIZU', 'VVPP'],
                ['FM', 'NE', 'NNE', 'NN']),
        PhraseletTemplate("verb-acc", "Jemand tut eine Sache", 1, 3,
                ['oa'],
                ['VMFIN', 'VMINF', 'VMPP', 'VVFIN', 'VVIMP', 'VVINF', 'VVIZU', 'VVPP'],
                ['FM', 'NE', 'NNE', 'NN']),
        PhraseletTemplate("verb-dat", "Jemand gibt einer Sache etwas", 1, 3,
                ['da'],
                ['VMFIN', 'VMINF', 'VMPP', 'VVFIN', 'VVIMP', 'VVINF', 'VVIZU', 'VVPP'],
                ['FM', 'NE', 'NNE', 'NN']),
        PhraseletTemplate("noun-dependent", "Eine beschriebene Sache", 2, 1,
                ['nk', 'ag'],
                ['FM', 'NE', 'NNE', 'NN'],
                ['FM', 'NE', 'NNE', 'NN', 'ADJA', 'ADJD', 'ADV']),
        PhraseletTemplate("verb-adverb", "schnell machen", 1, 0,
                ['mo', 'oc'],
                ['VMFIN', 'VMINF', 'VMPP', 'VVFIN', 'VVIMP', 'VVINF', 'VVIZU', 'VVPP'],
                ['ADJA', 'ADJD', 'ADV']),
        PhraseletTemplate("word", "Sache", 0, None,
                None,
                ['FM', 'NE', 'NNE', 'NN'],
                None)]

    phraselet_stop_lemmas = ['sein', 'haben']

    def _set_negation(self, token):
        """Marks the negation on the token. A token is negative if it or one of its ancestors
            has a negation word as a syntactic (not semantic!) child.
        """
        if token._.holmes.is_negated != None:
            return
        for child in token.children:
            if child._.holmes.lemma in ('nicht', 'kein', 'keine', 'nie') or \
                    child._.holmes.lemma.startswith('nirgend'):
                token._.holmes.is_negated = True
                return
        if token.dep_ == 'ROOT':
            token._.holmes.is_negated = False
            return
        self._set_negation(token.head)
        token._.holmes.is_negated = token.head._.holmes.is_negated

    def _correct_auxiliaries_and_passives(self, token):
        """Wherever auxiliaries and passives are found, derive the semantic information
            from the syntactic information supplied by spaCy.
        """

        def correct_auxiliaries_and_passives_recursively(token, processed_auxiliary_indexes):
            if token.i not in processed_auxiliary_indexes:
                processed_auxiliary_indexes.append(token.i)
                if token.pos_ == 'AUX' or token.tag_.startswith('VM'):
                    for dependency in (dependency for dependency in token._.holmes.children
                    if token.doc[dependency.child_index].pos_ in ('VERB', 'AUX') and
                    token.doc[dependency.child_index].dep_ in ('oc', 'pd')):
                        child = token.doc[dependency.child_index]
                        self._move_information_between_tokens(token, child)
                        # VM indicates a modal verb, which has to be marked as uncertain
                        if token.tag_.startswith('VM') or dependency.is_uncertain:
                            for child_dependency in child._.holmes.children:
                                child_dependency.is_uncertain = True
                        # passive construction
                        if (token._.holmes.lemma == 'werden' and child.tag_ not in ('VVINF',
                                'VAINF', 'VAFIN', 'VAINF')):
                            for child_or_sib in \
                                    child._.holmes.loop_token_and_righthand_siblings(token.doc):
                                #mark syntactic subject as semantic object
                                for grandchild_dependency in [grandchild_dependency for
                                        grandchild_dependency in child_or_sib._.holmes.children
                                        if grandchild_dependency.label == 'sb']:
                                    grandchild_dependency.label = 'oa'
                                #mark syntactic object as synctactic subject, removing the
                                #preposition 'von' or 'durch' from the construction and marking
                                #it as non-matchable
                                for grandchild_dependency in child_or_sib._.holmes.children:
                                    grandchild = token.doc[grandchild_dependency.child_index]
                                    if (grandchild_dependency.label == 'sbp' and
                                            grandchild._.holmes.lemma in ('von', 'vom')) or \
                                            (grandchild_dependency.label == 'mo' and
                                            grandchild._.holmes.lemma == 'durch'):
                                        grandchild._.holmes.is_matchable = False
                                        for great_grandchild_dependency in \
                                                grandchild._.holmes.children:
                                            if child_or_sib.i != \
                                                    great_grandchild_dependency.child_index:
                                                child_or_sib._.holmes.children.append(
                                                        SemanticDependency(child_or_sib.i,
                                                        great_grandchild_dependency.child_index,
                                                        'sb', dependency.is_uncertain))
                                        child_or_sib._.holmes.remove_dependency_with_child_index(
                                                grandchild_dependency.child_index)
            for syntactic_child in token.children:
                correct_auxiliaries_and_passives_recursively(syntactic_child,
                        processed_auxiliary_indexes)

        if token.dep_ == 'ROOT':
            correct_auxiliaries_and_passives_recursively(token, [])

    def _handle_relative_constructions(self, token):
        """Wherever auxiliaries and passives are found, derive the semantic information
            from the syntactic information supplied by spaCy.
        """
        for dependency in (dependency for dependency in token._.holmes.children if
                dependency.child_token(token.doc).tag_ in ('PRELS', 'PRELAT') and
                dependency.child_token(token.doc).dep_ != 'par'):
            counter = dependency.child_index
            while counter > token.sent.start:
                # find the antecedent
                counter -= 1
                working_token = token.doc[counter]
                if working_token.pos_ in ('NOUN', 'PROPN') and working_token.dep_ not in \
                        self.sibling_marker_deps:
                    working_dependency = None
                    for antecedent in (antecedent for antecedent in \
                            working_token._.holmes.loop_token_and_righthand_siblings(token.doc) \
                            if antecedent.i != token.i):
                        # add new dependency from the verb to the antecedent
                        working_dependency = SemanticDependency(
                            token.i, antecedent.i, dependency.label, True)
                        token._.holmes.children.append(working_dependency)
                    # the last antecedent before the pronoun is not uncertain, so reclassify it
                    if working_dependency != None:
                        working_dependency.is_uncertain = False
                        # remove the dependency from the verb to the relative pronoun
                        token._.holmes.remove_dependency_with_child_index(
                            dependency.child_index)
                        # label the relative pronoun as a grammatical token pointing to its
                        # direct antecedent
                        dependency.child_token(token.doc)._.holmes.children = [SemanticDependency(
                                dependency.child_index,
                                0 - (working_dependency.child_index + 1),
                                None)]

    def _holmes_lemma(self, token):
        """Relabel the lemmas of separable verbs in sentences like 'er steht auf' to incorporate
            the entire separable verb to facilitate matching.
        """
        if token.pos_ == 'VERB' and token.tag_ not in ('VAINF', 'VMINF', 'VVINF', 'VVIZU'):
            for child in token.children:
                if child.tag_ == 'PTKVZ':
                    child_lemma = child.lemma_.lower()
                    if child_lemma == 'einen':
                        child_lemma = 'ein'
                    return ''.join([child_lemma, token.lemma_.lower()])
        if token.tag_ == 'APPRART':
            if token.lemma_.lower() == 'im':
                return 'in'
            if token.lemma_.lower() == 'am':
                return 'an'
            if token.lemma_.lower() == 'beim':
                return 'bei'
            if token.lemma_.lower() == 'zum':
                return 'zu'
            if token.lemma_.lower() == 'zur':
                return 'zu'
        # sometimes adjectives retain their inflectional endings
        if token.tag_ == 'ADJA' and len(token.lemma_.lower()) > 5 and \
                token.lemma_.lower().endswith('en'):
            return token.lemma_.lower().rstrip('en')
        if token.tag_ == 'ADJA' and len(token.lemma_.lower()) > 5 and \
                token.lemma_.lower().endswith('e'):
            return token.lemma_.lower().rstrip('e')
        return token.lemma_.lower()

    def _perform_language_specific_tasks(self, token):

        # Because separable verbs are conflated into a single lemma, remove the dependency
        # from the verb to the preposition
        if token.tag_ == 'PTKVZ' and token.head.pos_ == 'VERB' and \
                token.head.tag_ not in ('VAINF', 'VMINF', 'VVINF', 'VVIZU'):
            token.head._.holmes.remove_dependency_with_child_index(token.i)

        # equivalence between 'der Abschluss einer Versicherung' and 'der Abschluss von einer
        # Versicherung': add an additional dependency spanning the preposition
        for dependency in (dependency for dependency in token._.holmes.children
                if dependency.label in ('mnr','pg') and
                dependency.child_token(token.doc)._.holmes.lemma in ('von', 'vom')):
            child = dependency.child_token(token.doc)
            for child_dependency in (child_dependency for child_dependency in
                    child._.holmes.children if child_dependency.label == 'nk' and
                    token.i != child_dependency.child_index):
                token._.holmes.children.append(SemanticDependency(
                    token.i, child_dependency.child_index, 'nk'))
                child._.holmes.is_matchable = False

        # Loop through the structure around a dependent verb to find the lexical token at which
        # to add new dependencies, and find out whether it is active or passive so we know
        # whether to add an 'sb' or an 'oa'.
        def find_target_tokens_and_dependency_recursively(token, visited=[]):
            visited.append(token.i)
            tokens_to_return = []
            target_dependency = 'sb'
            # Loop through grammatical tokens. 'dependency.child_index + token.i != -1' would mean
            # a grammatical token were pointing to itself (should never happen!)
            if len([dependency for dependency in token._.holmes.children
                    if dependency.child_index < 0 and dependency.child_index + token.i != -1]) > 0:
                for dependency in (dependency for dependency in token._.holmes.children
                        if dependency.child_index < 0 and dependency.child_index + token.i != -1):
                    # resolve the grammatical token pointer
                    child_token = token.doc[0 - (dependency.child_index + 1)]
                    # passive construction
                    if (token._.holmes.lemma == 'werden' and child_token.tag_ not in
                            ('VVINF', 'VAINF', 'VAFIN', 'VAINF')):
                        target_dependency = 'oa'
                    if child_token.i not in visited:
                        new_tokens, new_target_dependency = \
                                find_target_tokens_and_dependency_recursively(child_token, visited)
                        tokens_to_return.extend(new_tokens)
                        if new_target_dependency == 'oa':
                            target_dependency = 'oa'
                    else:
                        tokens_to_return.append(token)
            else:
                # we have reached the target token
                tokens_to_return.append(token)
            return tokens_to_return, target_dependency

        # 'Der Mann hat xxx, es zu yyy' and similar structures
        for dependency in (dependency for dependency in token._.holmes.children
                if dependency.label in ('oc', 'oa', 'mo', 're') and
                token.pos_ == 'VERB' and dependency.child_token(token.doc).pos_ in ('VERB', 'AUX')):
            dependencies_to_add = []
            target_tokens, target_dependency = find_target_tokens_and_dependency_recursively(
                    dependency.child_token(token.doc))
            # with um ... zu structures the antecedent subject is always the subject of the
            # dependent clause, unlike with 'zu' structures without the 'um'
            if len([other_dependency for other_dependency in target_tokens[0]._.holmes.children
                    if other_dependency.child_token(token.doc)._.holmes.lemma == 'um' and
                    other_dependency.child_token(token.doc).tag_ == 'KOUI']) == 0:
                # er hat ihm vorgeschlagen, etwas zu tun
                for other_dependency in (other_dependency for other_dependency
                        in token._.holmes.children if other_dependency.label == 'da'):
                    dependencies_to_add.append(other_dependency)
                if len(dependencies_to_add) == 0:
                    # er hat ihn gezwungen, etwas zu tun
                    # We have to distinguish this type of 'oa' relationship from dependent
                    # clauses and reflexive pronouns ('er entschied sich, ...')
                    for other_dependency in (other_dependency for other_dependency
                            in token._.holmes.children if other_dependency.label == 'oa' and
                            other_dependency.child_token(token.doc).pos_ not in ('VERB', 'AUX') and
                            other_dependency.child_token(token.doc).tag_ != 'PRF'):
                        dependencies_to_add.append(other_dependency)
            if len(dependencies_to_add) == 0:
                # We haven't found any object dependencies, so take the subject dependency
                for other_dependency in (other_dependency for other_dependency
                        in token._.holmes.children if other_dependency.label == 'sb'):
                    dependencies_to_add.append(other_dependency)
            for target_token in target_tokens:
                for other_dependency in (other_dependency for other_dependency in
                        dependencies_to_add if target_token.i != other_dependency.child_index):
                    # these dependencies are always uncertain
                    target_token._.holmes.children.append(SemanticDependency(
                        target_token.i, other_dependency.child_index, target_dependency, True))

        # 'er war froh, etwas zu tun'
        for dependency in (dependency for dependency in token._.holmes.children
                if dependency.label == 'nk' and token.pos_ == 'NOUN' and token.dep_ == 'sb' and
                dependency.child_token(token.doc).pos_ == 'ADJ'):
            child_token = dependency.child_token(token.doc)
            for child_dependency in (child_dependency for child_dependency in
                    child_token._.holmes.children if child_dependency.label in ('oc', 're') and
                    child_dependency.child_token(token.doc).pos_ in ('VERB', 'AUX')):
                target_tokens, target_dependency = find_target_tokens_and_dependency_recursively(
                        child_dependency.child_token(token.doc))
                for target_token in (target_token for target_token in target_tokens
                        if target_token.i != dependency.parent_index):
                    # these dependencies are always uncertain
                    target_token._.holmes.children.append(SemanticDependency(
                        target_token.i, dependency.parent_index, target_dependency, True))
