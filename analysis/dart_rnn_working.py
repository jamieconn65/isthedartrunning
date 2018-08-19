import pandas as pd
import numpy as np
#%matplotlib inline
import matplotlib.pyplot as plt
import sqlite3
import os
import sys

# local modules
module_path = os.path.abspath(os.path.join('../python'))
if module_path not in sys.path:
    sys.path.append(module_path)

import modelLib
from logfuncts import logger

df = modelLib.load_dataframe_from_sql("dart", limit=-1)

df = modelLib.preprocessing(df)

# take a nice section 
start_date = "2017-08-01 00:00:00"
end_date = "2017-11-01 00:00:00"
df = df[(df.index>=start_date) & (df.index < end_date)]

# some null values but let's just set them to base level 0.4
df[df.level.isnull()] = 0.4

# also don't believe that there was 20mm of rain in 15 minutes....

df.loc[df.model_rain > 15, 'model_rain'] = 0

import tensorflow as tf

num_steps = 40 #10 hours
batch_size = 300
num_classes = 2
state_size = 15
learning_rate = 0.1
update_timestep = 100
num_epochs = 20

def gen_real_data():
    X = np.array(1000*list(df.model_rain.values))
    Y = np.array(1000*list(df.level.values))
    update_vector = np.array([1 if (val%update_timestep==0) else 0 for val in range(len(X))])
    X = np.column_stack([X, update_vector, update_vector*Y])
    return X, Y

def gen_batch(raw_data, batch_size, num_steps):
    # adapted from https://github.com/tensorflow/tensorflow/blob/master/tensorflow/models/rnn/ptb/reader.py
    #print "batch_size:", batch_size
    #print "num_steps:", num_steps
    raw_x, raw_y = raw_data
    data_length = raw_x.shape[0]
    num_features = raw_x.shape[1]
    #print "data_length:", data_length

    #num_features = raw_x.shape[1]

    # partition raw data into batches and stack them vertically in a data matrix
    batch_partition_length = data_length // batch_size
    #print "batch_partition_length = data_length // batch_size = ", batch_partition_length

    data_x = np.zeros([batch_size, batch_partition_length, num_features], dtype=np.float32)
    data_y = np.zeros([batch_size, batch_partition_length], dtype=np.float32)
    for i in range(batch_size):
        data_x[i] = raw_x[batch_partition_length * i:batch_partition_length * (i + 1),:]
        data_y[i] = raw_y[batch_partition_length * i:batch_partition_length * (i + 1)]

    #print "data_x.shape, data_y.shape", data_x.shape, data_y.shape
    # further divide batch partitions into num_steps for truncated backprop
    epoch_size = batch_partition_length // num_steps
    #print "epoch_size = batch_partition_length // num_steps =", epoch_size

    for i in range(epoch_size):
        x = data_x[:, i * num_steps:(i + 1) * num_steps,:]
        y = data_y[:, i * num_steps:(i + 1) * num_steps]
        #print "x.shape, y.shape:", x.shape, y.shape
        yield (x, y)

    #print "There are 5 batches in each epoch. Each batch contains 200*10 values"
    
def gen_epochs(num_epochs, num_steps):
    for i in range(num_epochs):
        yield gen_batch(gen_real_data(), batch_size, num_steps)
        

"""
Placeholders
"""
num_features = 3
x = tf.placeholder(tf.float32, [batch_size, num_steps, num_features], name='input_placeholder')
y = tf.placeholder(tf.float32, [batch_size, num_steps], name='labels_placeholder')
init_state = tf.zeros([batch_size, state_size])

"""
RNN Inputs
"""

# Turn our x placeholder into a list of one-hot tensors:
# rnn_inputs is a list of num_steps tensors with shape [batch_size, num_classes]
#x_one_hot = tf.one_hot(x, num_classes)
rnn_inputs = tf.unstack(tf.reshape(x, (x.shape[0], x.shape[1], x.shape[2])), axis=1)

print x
print
print len(rnn_inputs)
print rnn_inputs


"""
Definition of rnn_cell

This is very similar to the __call__ method on Tensorflow's BasicRNNCell. See:
https://github.com/tensorflow/tensorflow/blob/master/tensorflow/contrib/rnn/python/ops/core_rnn_cell_impl.py#L95
"""
with tf.variable_scope('rnn_cell'):
    W = tf.get_variable('W', [num_features + state_size, state_size])
    b = tf.get_variable('b', [state_size], initializer=tf.constant_initializer(0.0))
    #Wy = tf.get_variable('Wy', [state_size, state_size])
    #by = tf.get_variable('by', [state_size], initializer=tf.constant_initializer(0.0))
    Wy = tf.get_variable('Wy', [state_size, 1])
    by = tf.get_variable('by', [1], initializer=tf.constant_initializer(0.0))
    
def rnn_cell(rnn_input, state):
    print "rnn_input", rnn_input.shape
    print "state", state.shape
    with tf.variable_scope('rnn_cell', reuse=True):
        W = tf.get_variable('W', [num_features + state_size, state_size])
        print "W", W.shape
        b = tf.get_variable('b', [state_size], initializer=tf.constant_initializer(0.0))
        #Wy = tf.get_variable('Wy', [state_size, state_size])
        #by = tf.get_variable('by', [state_size], initializer=tf.constant_initializer(0.0))
        Wy = tf.get_variable('Wy', [state_size, 1])
        by = tf.get_variable('by', [1], initializer=tf.constant_initializer(0.0))
        state = tf.tanh(tf.matmul(tf.concat([rnn_input, state], 1), W) + b)
        #output = tf.tanh(tf.matmul(state, Wy) + by)
        output = tf.matmul(state, Wy) + by
    return state, output




"""
Adding rnn_cells to graph

This is a simplified version of the "static_rnn" function from Tensorflow's api. See:
https://github.com/tensorflow/tensorflow/blob/master/tensorflow/contrib/rnn/python/ops/core_rnn.py#L41
Note: In practice, using "dynamic_rnn" is a better choice that the "static_rnn":
https://github.com/tensorflow/tensorflow/blob/master/tensorflow/python/ops/rnn.py#L390
"""
state = init_state
rnn_outputs = []
for rnn_input in rnn_inputs:
    state, output = rnn_cell(rnn_input, state)
    rnn_outputs.append(output)
final_state = state


"""
Predictions, loss, training step

Losses is similar to the "sequence_loss"
function from Tensorflow's API, except that here we are using a list of 2D tensors, instead of a 3D tensor. See:
https://github.com/tensorflow/tensorflow/blob/master/tensorflow/contrib/seq2seq/python/ops/loss.py#L30
"""


predictions = tf.squeeze(rnn_outputs)

# Turn our y placeholder into a list of labels
y_as_list = tf.unstack(y, num=num_steps, axis=1)


#losses and train_step

#losses = [tf.nn.sparse_softmax_cross_entropy_with_logits(labels=label, logits=logit) for \
          #logit, label in zip(logits, y_as_list)

losses = tf.losses.mean_squared_error(y_as_list, predictions)
total_loss = tf.reduce_mean(losses)
train_step = tf.train.AdagradOptimizer(learning_rate).minimize(total_loss)



"""
Train the network
"""

def train_network(num_epochs, num_steps, state_size, verbose=True):
    with tf.Session() as sess:
        sess.run(tf.global_variables_initializer())
        training_losses = []
        preds = []
        Xs = []
        Ys = []
        for idx, epoch in enumerate(gen_epochs(num_epochs, num_steps)):
            training_loss = 0
            training_state = np.zeros((batch_size, state_size))
            
            if verbose:
                print "EPOCH", idx
            for step, (X, Y) in enumerate(epoch):
                #print X.shape, Y.shape
                tr_losses, training_loss_, training_state, _, y_test, pred_test = \
                    sess.run([losses,
                              total_loss,
                              final_state,
                              train_step,
                              y_as_list,
                              predictions],
                                  feed_dict={x:X, y:Y, init_state:training_state})
                training_loss += training_loss_
                preds += list(pred_test.T[-1])
                Ys += list(Y[-1])
                
                if step % 100 == 0 and step > 0:
                    if verbose:
                        print("Average loss at step", step,
                              "for last 100 steps:", training_loss/100)
                    training_losses.append(training_loss/100)
                    training_loss = 0
            
    return training_losses, X, Y, pred_test, preds, Ys, Xs, y_test

training_losses, final_X, final_Y, final_predictions, predictions, Ys, Xs, y_test = train_network(num_epochs,num_steps, state_size)
plt.plot(training_losses)
plt.show()


plt.plot(predictions[-2000:])
plt.plot(Ys[-2000:])
plt.savefig('graph.png', dpi=400)
plt.show()

from math import sqrt

errors = np.abs(np.array(Ys) - np.array(predictions))
print "Mean absolute error:", errors.mean().round(4)
print "Root mean squared error:", round(sqrt((errors ** 2).mean()), 3)