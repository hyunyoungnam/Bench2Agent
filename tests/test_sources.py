"""BibTeX parsing for the Anthology and PMLR adapters in icml.sources."""
import unittest

from icml.sources import bib_authors, delatex, parse_bibtex

ANTHOLOGY = '''@inproceedings{li-etal-2024-x,
    title = "{BERT} Meets {M}{\\"u}ller",
    author = "M{\\"u}ller, Jos{\\'e}  and
      Li, Wei",
    url = "https://aclanthology.org/2024.acl-long.12/",
    abstract = "Nested {braces {inside}} and 50\\% gains.",
}
'''

PMLR = '''@InProceedings{pmlr-v229-a23a,
  title = 	 {Expansive {L}atent Planning},
  author =       {Gieselmann, Robert and Pokorny, Florian T.},
  year = 	 {2023}
}
'''


class BibtexTests(unittest.TestCase):
    def test_quoted_values_with_nested_braces(self):
        e = parse_bibtex(ANTHOLOGY)[0]
        self.assertEqual(e["_key"], "li-etal-2024-x")
        self.assertEqual(delatex(e["title"]), "BERT Meets Müller")
        self.assertEqual(delatex(e["abstract"]), "Nested braces inside and 50% gains.")
        self.assertEqual(e["url"], "https://aclanthology.org/2024.acl-long.12/")

    def test_last_first_authors_become_first_last(self):
        e = parse_bibtex(ANTHOLOGY)[0]
        self.assertEqual(bib_authors(e["author"]), ["José Müller", "Wei Li"])

    def test_braced_values_and_bare_numbers(self):
        e = parse_bibtex(PMLR)[0]
        self.assertEqual(delatex(e["title"]), "Expansive Latent Planning")
        self.assertEqual(e["year"], "2023")
        self.assertEqual(bib_authors(e["author"]), ["Robert Gieselmann", "Florian T. Pokorny"])


if __name__ == "__main__":
    unittest.main()
