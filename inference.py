from gritlm import GritLM
from peft import PeftModel
from collections import defaultdict
from parser import parse_args
from tqdm import tqdm

import torch.nn.functional as F
import os
import json
import torch
import numpy as np

from prompter import Prompter


def gritlm_instruction(instruction):
    return "<|user|>\n" + instruction + "\n<|embed|>\n" if instruction else "<|embed|>\n"


def recall_score(gt_list, pred_list, ks,verbose=True):
    hits = defaultdict(list)
    for gt, preds in zip(gt_list, pred_list):
        for k in ks:
            hits[k].append(len(list(set(gt).intersection(set(preds[:k]))))/len(gt))
    if verbose:
        for k in ks:
            print("Recall@{}: {:.4f}".format(k, np.mean(hits[k])))
    return hits


def inference(args):
    model_path = os.path.join(args.home, 'model_weights', args.target_model_path)
    data_path = os.path.join(args.home, 'training/crs_data', args.data_json)
    db_path = os.path.join(args.home, 'training/crs_data', args.db_json)
    embeddings_path = os.path.join(args.home, 'training/crs_data', args.embeddings_path)
    saved_time = model_path.split('/')[-2]
    to_json = os.path.join(args.home, 'results', f"{saved_time}_{args.to_json}.jsonl")

    # Loads the model for both capabilities; If you only need embedding pass `mode="embedding"` to save memory (no lm head)
    model = GritLM("GritLM/GritLM-7B", mode='embedding', torch_dtype="auto")
    # model = GritLM("GritLM/GritLM-7B", torch_dtype="auto")
    model.model = PeftModel.from_pretrained(model.model, model_path)
    # merged_model = lora_model.merge_and_unload()
    # model.model = lora_model
    # [(i,k) for i,k in lora_model.named_parameters()][0]
    prompter = Prompter(args)
    query_instr, doc_instr = prompter.get_instruction()

    ### Embedding/Representation ### /home/user/junpyo/gritlm/training/crs_data/test_processed_title.jsonl
    with open(data_path) as fd:
        lines = fd.readlines()
        test_data = [json.loads(line) for line in lines]
        print(len(test_data))
    
    queries = [prompter.generate_prompt(i) for i in test_data]
    labels = [i['rec'][0] for i in test_data]

    db = json.load(open(db_path, 'r', encoding='utf-8'))
    documents = list(db.values())
    documents = [doc[:prompter.max_char_len * 10] for doc in documents]
    print(len(documents))
    
    all_names = list(db.keys())
    name2id = {all_names[index]: index for index in range(len(all_names))}
    print("name2id:",len(name2id))
    id2name = {v:k for k,v in name2id.items()}

    rec_lists = [[name2id[i]] for i in labels]

    # if os.path.exists(embeddings_path):
    #     print("loading embeddings form file")
    #     d_rep = torch.load(embeddings_path)
    # else:
    d_rep= []
    for i in tqdm(range(0, len(documents), 64)):
        batch_documents = documents[i: i + 64]
        d_rep.append(model.encode(batch_documents, instruction=gritlm_instruction(doc_instr)))
    d_rep=np.concatenate(d_rep, axis=0)
    torch.save(d_rep, embeddings_path)
    # print(d_rep[0][:100])
    print('document shape:',torch.from_numpy(d_rep).shape)
    # print("saving embeddigns to file ...") 

    rank = []

    for i in tqdm(range(0, len(queries), args.batch_size)):
        batch_queries = queries[i: i + args.batch_size]
        q_rep = model.encode(batch_queries, instruction=gritlm_instruction(query_instr))

        # print('queries shape:', torch.from_numpy(q_rep).shape) 

        cos_sim = F.cosine_similarity(torch.from_numpy(q_rep).unsqueeze(1), torch.from_numpy(d_rep).unsqueeze(0),dim=-1)
        cos_sim = torch.where(torch.isnan(cos_sim), torch.full_like(cos_sim,0), cos_sim)
        # print("cos_sim shape:", cos_sim.shape)
        # print("cos_sim:", cos_sim)

        topk_sim_values, topk_sim_indices = torch.topk(cos_sim,k=50,dim=-1)
        rank_slice = topk_sim_indices.tolist()
        rank += rank_slice
        # print('length rank:',len(rank))

    print('length rank:',len(rank))
    recall_score(rec_lists, rank, ks=[1,3,5,10,20,50])

    for i in range(len(rank)):

        ranked_list = {j:id2name[j] for j in rank[i]}

        test_data[i]["cand_list"] = ranked_list

        with open(to_json, "w", encoding="utf-8") as fwr:
            for example in test_data:
                fwr.write(json.dumps(example))
                fwr.write("\n")



if __name__ == '__main__':
    args = parse_args()
    inference(args)