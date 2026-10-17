import sys
import tempfile
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from app_common import copy_exact
from clothing import verified_match, garment_features


def outfit(upper,lower):
    value = np.zeros((2,10),np.float32)
    value[0,upper]=1
    value[1,lower]=1
    return value


class ClothingTests(unittest.TestCase):
    def setUp(self):
        self.data={'embeddings':np.array([[1.,0.],[1.,0.]]),'tracks':np.array([1,1]),'times':np.array([0.,.2])}
        self.ref={'embeddings':np.array([[1.,0.]]),'clothes':np.array([outfit(0,8)]),'valid':np.ones((1,2),bool)}

    def test_matching_clothes_and_two_frames_pass(self):
        result=verified_match(self.data,self.ref,np.array([outfit(0,8)]*2),np.ones((2,2),bool))
        self.assertTrue(result['passed'])

    def test_perfect_reid_cannot_override_wrong_clothes(self):
        result=verified_match(self.data,self.ref,np.array([outfit(3,6)]*2),np.ones((2,2),bool))
        self.assertFalse(result['passed'])

    def test_upper_and_lower_must_match_same_reference(self):
        ref={'embeddings':np.array([[1.,0.],[1.,0.]]),'clothes':np.array([outfit(0,6),outfit(3,8)]),'valid':np.ones((2,2),bool)}
        result=verified_match(self.data,ref,np.array([outfit(0,8)]*2),np.ones((2,2),bool))
        self.assertFalse(result['passed'])

    def test_invisible_clothes_are_not_accepted(self):
        result=verified_match(self.data,self.ref,np.array([outfit(0,8)]*2),np.zeros((2,2),bool))
        self.assertFalse(result['passed'])

    def test_different_people_cannot_supply_two_supporting_frames(self):
        self.data['tracks']=np.array([1,2])
        result=verified_match(self.data,self.ref,np.array([outfit(0,8)]*2),np.ones((2,2),bool))
        self.assertFalse(result['passed'])

    def test_body_mask_ignores_red_background(self):
        crop=np.full((300,150,3),(0,0,255),np.uint8)
        crop[45:145,50:100]=(25,25,25)
        crop[155:275,50:100]=(170,100,60)
        points=np.zeros((17,3),np.float32)
        for i,xy in {5:(60,40),6:(90,40),11:(60,150),12:(90,150),13:(60,220),14:(90,220),15:(60,290),16:(90,290)}.items():
            points[i]=[*xy,.99]
        hist,valid,_=garment_features(crop,points)
        self.assertTrue(valid.all())
        self.assertGreater(hist[0,0],.9)
        self.assertGreater(hist[1,8],.9)

    def test_copy_is_exact_and_existing_different_file_is_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            src,dst=Path(directory)/'input.ts',Path(directory)/'output.ts'
            src.write_bytes(b'original\x00\xff')
            copy_exact(src,dst)
            self.assertEqual(src.read_bytes(),dst.read_bytes())
            dst.write_bytes(b'different')
            with self.assertRaises(ValueError):
                copy_exact(src,dst)


if __name__=='__main__':
    unittest.main()
